"""Verify GeoTIFF workflows through the real persistent engine and workers."""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib
import json
import warnings
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.enums import ColorInterp
from rasterio.io import MemoryFile
from rasterio.transform import Affine

Rpc = importlib.import_module("verify-vectors").Rpc
BOUNDS = [12600000, 3150000, 12600400, 3150400]
TRANSFORM = Affine(100, 0, BOUNDS[0], 0, -100, BOUNDS[3])


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def fixtures(directory: Path) -> dict[str, str]:
    directory.mkdir()
    paths = {name: str(directory / f"{name}.tif") for name in ("gray", "rgb", "unknown", "rotated")}
    values = np.arange(16, dtype=np.float32).reshape(4, 4)
    values[0, 0], values[0, 1] = -9999, 0
    with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
        with rasterio.open(paths["gray"], "w", driver="GTiff", width=4, height=4, count=1,
                           dtype="float32", crs="EPSG:3857", transform=TRANSFORM, nodata=-9999) as target:
            target.write(values, 1)
            target.scales, target.offsets = (2,), (100,)
            target.set_band_unit(1, "m")
            target.set_band_description(1, "Synthetic elevation")
            target.update_tags(AREA_OR_POINT="Area", origin="synthetic acceptance")
            mask = np.full((4, 4), 255, dtype=np.uint8)
            mask[3, 0] = 0
            target.write_mask(mask)
        rgb = np.stack([np.full((4, 4), 40, dtype=np.uint8), np.full((4, 4), 120, dtype=np.uint8),
                        np.full((4, 4), 220, dtype=np.uint8)])
        with rasterio.open(paths["rgb"], "w", driver="GTiff", width=4, height=4, count=3,
                           dtype="uint8", crs="EPSG:3857", transform=TRANSFORM) as target:
            target.write(rgb)
            target.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue)
        with rasterio.open(paths["unknown"], "w", driver="GTiff", width=4, height=4, count=1,
                           dtype="float32", transform=TRANSFORM) as target:
            target.write(values, 1)
        with rasterio.open(paths["rotated"], "w", driver="GTiff", width=4, height=4, count=1,
                           dtype="float32", crs="EPSG:4547", transform=Affine(20, 3, 500000, 2, -20, 3000100)) as target:
            target.write(values, 1)
    return paths


def png_pixels(result: dict) -> np.ndarray:
    assert result["mimeType"] == "image/png" and result["dataCrs"] == "EPSG:3857"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", rasterio.errors.NotGeoreferencedWarning)
        with MemoryFile(base64.b64decode(result["imageBase64"], validate=True)) as memory:
            with memory.open() as image:
                assert image.width == result["width"] and image.height == result["height"] and image.count == 4
                return image.read()


def rejected(rpc: Rpc, method: str, params: dict, kind: str) -> None:
    try:
        rpc.call(method, params)
    except AssertionError as error:
        assert kind in str(error), str(error)
    else:
        raise AssertionError(f"{method} should reject {kind}")


def verify(rpc: Rpc, output: Path, checks: list[str], export_directory: Path) -> dict:
    runtime = rpc.call("runtime.info")
    assert runtime["protocolVersion"] == 4 and runtime["engineVersion"] == "0.4.0", runtime
    project = rpc.call("project.create", {"directory": str(output / "project"), "name": "Raster acceptance"})
    assert project["schemaVersion"] == 4
    path = project["projectPath"]
    sources = fixtures(output / "sources")
    datasets = {}
    for name, source_path in sources.items():
        source = Path(source_path)
        original_hash = sha256(source)
        inspection = rpc.call("raster.inspect", {"sourcePath": source_path})
        assert inspection["driver"] == "GTiff" and inspection["raster"]["width"] == 4
        task = rpc.wait_task(path, rpc.call("raster.import", {"path": path, "sourcePath": source_path}))
        state = rpc.call("workspace.get", {"path": path})
        dataset = next(item for item in state["datasets"] if item["id"] == task["datasetId"])
        datasets[name] = dataset
        assert dataset["kind"] == "raster" and dataset["version"] == original_hash
        assert "featureCount" not in dataset and "fields" not in dataset
        assert dataset["raster"] == inspection["raster"]
        assert sha256(Path(path).parent / dataset["relativePath"]) == original_hash
        assert sha256(source) == original_hash
        layer = next(item for item in state["layers"] if item["datasetId"] == dataset["id"])
        assert "rasterStyle" in layer
        export = export_directory / f"{name}.tiff"
        rpc.wait_task(path, rpc.call("raster.export", {"path": path, "datasetId": dataset["id"], "destination": str(export)}))
        assert sha256(export) == original_hash
        assert rpc.call("raster.inspect", {"sourcePath": str(export)})["raster"] == inspection["raster"]
        rejected(rpc, "raster.export", {"path": path, "datasetId": dataset["id"], "destination": str(export)}, "destination_exists")
        checks.append(f"{name}: metadata, immutable snapshot, exact export and destination protection.")

    gray = datasets["gray"]
    query = {"path": path, "datasetId": gray["id"]}
    style = {"mode": "gray", "bands": [1], "ranges": [[0, 15]], "resampling": "nearest"}
    render_params = {**query, "bbox": BOUNDS, "width": 4, "height": 4, "style": style}
    image = rpc.call("raster.render", render_params)
    pixels = png_pixels(image)
    assert image["bbox"] == BOUNDS
    assert pixels[3, 0, 0] == 0 and pixels[3, 3, 0] == 0
    assert pixels[3, 0, 1] == 255 and pixels[0, 0, 1] == 0
    assert pixels[0, 3, 3] == 255 and pixels[3, 3, 3] == 255
    assert np.array_equal(pixels[0], pixels[1]) and np.array_equal(pixels[1], pixels[2])
    transformer = Transformer.from_crs(3857, 4326, always_xy=True)
    for row, column, raw, valid in [(0, 0, "-9999.0", False), (0, 1, "0.0", True), (3, 0, "12.0", False), (3, 3, "15.0", True)]:
        coordinate = transformer.transform(BOUNDS[0] + 100 * (column + 0.5), BOUNDS[3] - 100 * (row + 0.5))
        sample = rpc.call("raster.sample", {**query, "coordinate": coordinate})
        assert sample["pixel"] == {"row": row, "column": column} and sample["inside"]
        band = sample["bands"][0]
        assert band["rawValue"] == raw and band["valid"] == valid, sample
        if valid:
            assert band["value"] == float(raw) * 2 + 100
    outside = rpc.call("raster.sample", {**query, "coordinate": transformer.transform(BOUNDS[0] - 100, BOUNDS[1] - 100)})
    assert not outside["inside"] and outside["pixel"] is None
    checks.append("Gray PNG cells, NoData/internal mask, valid zero, original cell indices and scale/offset agree.")

    rgb_params = {**render_params, "datasetId": datasets["rgb"]["id"],
                  "style": {"mode": "rgb", "bands": [1, 2, 3], "ranges": [[0, 255]] * 3, "resampling": "nearest"}}
    rgb = png_pixels(rpc.call("raster.render", rgb_params))
    assert rgb[:, 2, 2].tolist() == [40, 120, 220, 255]
    checks.append("RGB band selection and exact channels survive PNG rendering.")

    rotated = datasets["rotated"]
    transform = Affine(*rotated["raster"]["transform"])
    x, y = transform * (2.5, 1.5)
    coordinate = Transformer.from_crs(4547, 4326, always_xy=True).transform(x, y)
    sample = rpc.call("raster.sample", {"path": path, "datasetId": rotated["id"], "coordinate": coordinate})
    assert sample["pixel"] == {"row": 1, "column": 2} and sample["bands"][0]["rawValue"] == "6.0"
    checks.append("Rotated CGCS2000 projected grid returns the independently known source cell.")

    unknown = datasets["unknown"]
    assert unknown["crsWkt"] is None and unknown["boundsWgs84"] is None and unknown["report"]["status"] == "restricted"
    rejected(rpc, "raster.render", {**render_params, "datasetId": unknown["id"]}, "crs_required")
    rejected(rpc, "raster.render", {**render_params, "width": 1025}, "invalid_params")
    rejected(rpc, "vector.viewport", {**query, "bbox": [113, 26, 115, 28], "limit": 2000, "propertyFields": []}, "invalid_dataset_kind")
    checks.append("Unknown CRS, oversized display requests and vector operations on rasters are rejected.")

    state = rpc.call("workspace.get", {"path": path})
    layer = next(item for item in state["layers"] if item["datasetId"] == gray["id"])
    updated = rpc.call("layer.update", {"path": path, "layerId": layer["id"], "changes": {"rasterStyle": style, "opacity": 0.65}})
    assert updated["rasterStyle"] == style and updated["opacity"] == 0.65
    ordered = [item["id"] for item in reversed(state["layers"])]
    rpc.call("layer.reorder", {"path": path, "layerIds": ordered})
    cancelled = rpc.call("raster.import", {"path": path, "sourcePath": sources["gray"]})
    cancelled = rpc.call("task.cancel", {"path": path, "taskId": cancelled["id"]})
    assert cancelled["status"] == "cancelled", cancelled
    state = rpc.call("workspace.get", {"path": path})
    assert len(state["datasets"]) == 4
    checks.append("Layer style/order persist and immediate import cancellation leaves no extra dataset.")

    for source_path in sources.values():
        source = Path(source_path)
        source.rename(source.with_name("moved-" + source.name))
    rpc.call("project.close")
    rpc.call("project.open", {"path": path})
    restored = rpc.call("workspace.get", {"path": path})
    assert restored["datasets"] == state["datasets"] and restored["layers"] == state["layers"]
    assert np.array_equal(png_pixels(rpc.call("raster.render", render_params)), pixels)
    rpc.call("project.close")
    checks.append("Reopen and render succeed after original source files move.")
    return {"runtime": runtime, "projectPath": path, "sources": sources, "exportDirectory": str(export_directory)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--executable")
    parser.add_argument("--export-directory")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    export_directory = Path(args.export_directory).resolve() if args.export_directory else output / "exports"
    export_directory.mkdir(parents=True, exist_ok=False)
    report = {"ok": False, "checks": []}
    rpc = Rpc(Path(__file__).resolve().parents[1], output, args.executable)
    try:
        report.update(verify(rpc, output, report["checks"], export_directory))
        if args.executable:
            assert report["runtime"]["packaged"] is True
        report["ok"] = True
    except Exception as error:
        report["error"] = repr(error)
        raise
    finally:
        rpc.close()
        (output / "raster-verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"ok": report["ok"], "checks": len(report["checks"]), "output": str(output)}))


if __name__ == "__main__":
    main()
