"""Exercise mixed project copies and source relocation through real RPC workers."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import runpy
import shutil
import time
from pathlib import Path

from pyproj import Transformer

vectors = importlib.import_module("verify-vectors")
tables = importlib.import_module("verify-tables")
rasters = importlib.import_module("verify-rasters")
Rpc = vectors.Rpc


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def terminal_task(rpc: Rpc, path: str, task: dict) -> dict:
    deadline = time.monotonic() + 180
    while task["status"] == "running":
        assert time.monotonic() < deadline, task
        time.sleep(0.1)
        task = rpc.call("task.get", {"path": path, "taskId": task["id"]})
    return task


def rejected(rpc: Rpc, method: str, params: dict) -> None:
    try:
        rpc.call(method, params)
    except AssertionError:
        return
    raise AssertionError(f"Expected {method} to reject the request")


def inspect_workspace(rpc: Rpc, project: dict, expected: dict) -> dict:
    path = project["projectPath"]
    workspace = rpc.call("workspace.get", {"path": path})
    assert workspace["projectId"] == project["id"]
    assert workspace["datasets"] == expected["datasets"]
    assert workspace["layers"] == expected["layers"]
    for dataset in workspace["datasets"]:
        managed = Path(path).parent / dataset["relativePath"]
        assert digest(managed) == dataset["version"]
        query = {"path": path, "datasetId": dataset["id"]}
        if dataset["kind"] == "raster":
            style = next(layer["rasterStyle"] for layer in workspace["layers"] if layer["datasetId"] == dataset["id"])
            image = rpc.call("raster.render", {**query, "bbox": rasters.BOUNDS, "width": 4, "height": 4, "style": style})
            pixels = rasters.png_pixels(image)
            assert pixels[3].max() == 255
            coordinate = Transformer.from_crs(3857, 4326, always_xy=True).transform(12600350, 3150050)
            sample = rpc.call("raster.sample", {**query, "coordinate": coordinate})
            assert sample["pixel"] == {"row": 3, "column": 3}
            assert sample["bands"][0]["valid"]
        else:
            page = rpc.call(f"{dataset['kind']}.page", {**query, "offset": 0, "limit": 200,
                            "sortField": None, "descending": False, "filter": None})
            assert page["total"] == dataset["featureCount"]
            assert page["rows"][0]["values"]["code"] == "001"
            if dataset["kind"] == "vector":
                view = rpc.call("vector.viewport", {**query, "bbox": dataset["boundsWgs84"], "limit": 2000, "propertyFields": ["code"]})
                assert view["returnedCount"] > 0 and not view["truncated"]
        if dataset["source"]["driver"] == "TablePoints":
            source = rpc.call("source.status", query)
            parent_id = dataset["source"]["metadata"]["parentDatasetId"]
            parent = next(item for item in workspace["datasets"] if item["id"] == parent_id)
            assert source["availability"] == "internal"
            assert Path(source["resolvedPath"]) == Path(path).parent / parent["relativePath"]
    return workspace


def verify(rpc: Rpc, output: Path, checks: list[str]) -> dict:
    runtime = rpc.call("runtime.info")
    assert runtime["protocolVersion"] == 6 and runtime["engineVersion"] == "0.6.1"
    project = rpc.call("project.create", {"directory": str(output / "original"), "name": "Mixed original"})
    path = project["projectPath"]
    sources = output / "sources"
    sources.mkdir()
    bundle = runpy.run_path(str(Path(__file__).resolve().parents[1] / "gis-engine/tests/vector_fixtures.py"))["create_fixture_bundle"](sources / "vectors")
    for kind, source in bundle.items():
        rpc.wait_task(path, rpc.call("vector.import", {"path": path, "sourcePath": source, "sourceLayer": "land",
                     "encoding": "GBK" if kind == "shp" else None, "assignedCrs": None}))
    options = tables.fixtures(sources / "tables")
    parent_id = None
    for option in [options[0], options[-1]]:
        task = rpc.wait_task(path, rpc.call("table.import", {"path": path, **option}))
        parent_id = parent_id or task["datasetId"]
    rpc.wait_task(path, rpc.call("table.points", {"path": path, "datasetId": parent_id, "xField": "x", "yField": "y", "declaredCrs": "EPSG:4326"}))
    grids = rasters.fixtures(sources / "rasters")
    for name in ["gray", "rgb"]:
        rpc.wait_task(path, rpc.call("raster.import", {"path": path, "sourcePath": grids[name]}))
    workspace = rpc.call("workspace.get", {"path": path})
    assert len(workspace["datasets"]) == 9
    layer = workspace["layers"][0]
    rpc.call("layer.update", {"path": path, "layerId": layer["id"], "changes": {"name": "Preserved layer", "opacity": 0.7, "visible": False}})
    rpc.call("layer.reorder", {"path": path, "layerIds": [item["id"] for item in reversed(workspace["layers"])]})
    workspace = rpc.call("workspace.get", {"path": path})
    checks.append("Four vector formats, CSV/XLSX, derived points and two GeoTIFFs form one 9-dataset workspace.")

    source_hashes = {str(item.relative_to(sources)): digest(item) for item in sources.rglob("*") if item.is_file()}
    relocated = output / "relocated-sources"
    shutil.copytree(sources, relocated)
    offline = output / "offline-originals"
    sources.rename(offline)
    for dataset in workspace["datasets"]:
        query = {"path": path, "datasetId": dataset["id"]}
        if dataset["source"]["driver"] == "TablePoints":
            continue
        status = rpc.call("source.status", query)
        assert status["availability"] == "missing" and status["verifiedAt"] is None
        candidate = relocated / Path(dataset["source"]["path"]).relative_to(sources)
        rpc.wait_task(path, rpc.call("source.relocate", {**query, "sourcePath": str(candidate)}))
        status = rpc.call("source.status", query)
        assert status["availability"] == "present" and status["relocated"] and status["verifiedAt"]
        assert Path(status["resolvedPath"]) == candidate
        assert status["originalPath"] == dataset["source"]["path"]
    assert rpc.call("workspace.get", {"path": path})["datasets"] == workspace["datasets"]
    checks.append("All eight external sources relocate by matching identity; original provenance and snapshot versions stay unchanged.")

    changed = output / "changed.csv"
    changed.write_text("code,x,y\n999,1,2\n", encoding="utf-8")
    failed = terminal_task(rpc, path, rpc.call("source.relocate", {"path": path, "datasetId": parent_id, "sourcePath": str(changed)}))
    assert failed["status"] == "failed", failed
    parent_status = rpc.call("source.status", {"path": path, "datasetId": parent_id})
    assert Path(parent_status["resolvedPath"]) != changed
    checks.append("Changed source content is rejected without replacing the last verified location.")

    copy_params = {"path": path, "directory": str(output / "copy"), "name": "Unsaved copy name",
                   "description": "Unsaved description is copied without saving the original.", "analysisCrs": "EPSG:4547",
                   "displayCrs": "EPSG:3857", "viewState": {"center": [113.8, 27.2], "zoom": 9}}
    existing = output / "existing"
    existing.mkdir()
    sentinel = existing / "keep.txt"
    sentinel.write_text("untouched", encoding="ascii")
    rejected(rpc, "project.saveAs", {**copy_params, "directory": str(existing)})
    rejected(rpc, "project.saveAs", {**copy_params, "directory": str(Path(path).parent / "nested")})
    assert sentinel.read_text(encoding="ascii") == "untouched"
    cancelled_dir = output / "cancelled-copy"
    cancelled = rpc.call("project.saveAs", {**copy_params, "directory": str(cancelled_dir)})
    cancelled = rpc.call("task.cancel", {"path": path, "taskId": cancelled["id"]})
    assert cancelled["status"] == "cancelled", cancelled
    assert not (cancelled_dir / "project.spa").exists()
    checks.append("Existing/nested targets are rejected and immediate copy cancellation publishes no project.")

    workspace = rpc.call("workspace.get", {"path": path})
    copy_task = rpc.wait_task(path, rpc.call("project.saveAs", copy_params))
    copied = rpc.call("project.open", {"path": copy_task["destination"]})
    assert copied["id"] != project["id"] and copied["schemaVersion"] == 6
    for key in ["name", "description", "analysisCrs", "displayCrs", "viewState"]:
        assert copied[key] == copy_params[key]
    inspect_workspace(rpc, copied, workspace)
    old_tasks = {task["id"] for task in workspace["tasks"] if task["status"] != "running"}
    copied_tasks = rpc.call("workspace.get", {"path": copied["projectPath"]})["tasks"]
    assert old_tasks <= {task["id"] for task in copied_tasks}
    restored_location = rpc.call("source.status", {"path": copied["projectPath"], "datasetId": parent_id})
    assert restored_location == parent_status
    checks.append("Save As preserves all nine exact snapshots, styles, task history, relocation history and current draft in a new project identity.")

    original_again = rpc.call("project.open", {"path": path})
    assert original_again["id"] == project["id"]
    for key in ["name", "description", "analysisCrs", "viewState"]:
        assert original_again[key] == project[key]
    inspect_workspace(rpc, original_again, workspace)
    rpc.call("project.close")
    moved = output / "moved-copy"
    Path(copied["projectPath"]).parent.rename(moved)
    copied = rpc.call("project.open", {"path": str(moved / "project.spa")})
    inspect_workspace(rpc, copied, workspace)
    checks.append("Original form metadata remains unchanged; a whole-directory move preserves queries, map rendering, pixel sampling and derived source resolution.")

    exports = output / "exports"
    exports.mkdir()
    for dataset in workspace["datasets"]:
        destination = exports / (dataset["id"] + (".tif" if dataset["kind"] == "raster" else ".gpkg"))
        rpc.wait_task(copied["projectPath"], rpc.call(f"{dataset['kind']}.export", {
            "path": copied["projectPath"], "datasetId": dataset["id"], "destination": str(destination)}))
        assert digest(destination) == dataset["version"]
    assert all(digest(offline / name) == value for name, value in source_hashes.items())
    rpc.call("project.close")
    checks.append("Every copied dataset exports with matching bytes after relocation; all original input bytes remain unchanged.")
    return {"runtime": runtime, "originalProject": path, "copiedProject": copied["projectPath"],
            "datasetCount": len(workspace["datasets"]), "sources": str(relocated), "exports": str(exports)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--executable")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {"ok": False, "checks": []}
    rpc = Rpc(Path(__file__).resolve().parents[1], output, args.executable)
    try:
        report.update(verify(rpc, output, report["checks"]))
        if args.executable:
            assert report["runtime"]["packaged"] is True
        report["ok"] = True
    except Exception as error:
        report["error"] = repr(error)
        raise
    finally:
        rpc.close()
        (output / "portability-verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"ok": report["ok"], "checks": len(report["checks"]), "output": str(output)}))


if __name__ == "__main__":
    main()
