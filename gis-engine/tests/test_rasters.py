from __future__ import annotations

import base64
import hashlib
import json
import sys
import time
import uuid
import warnings
from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import Transformer
from rasterio.enums import ColorInterp, Resampling
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from spatial_engine import rasters
from spatial_engine.errors import DomainError, InvalidParamsError
from spatial_engine.projects import ProjectStore
from spatial_engine.tasks import TaskManager
from spatial_engine.workspace import WorkspaceStore
from raster_fixtures import create_raster_fixture_bundle


@pytest.fixture
def sources(tmp_path):
    return create_raster_fixture_bundle(tmp_path / "sources")


def import_raster(path, work_dir, **kwargs):
    return rasters.import_raster({"sourcePath": str(path), "datasetId": str(uuid.uuid4())}, work_dir,
                                 progress=kwargs.get("progress", lambda *args: None), cancelled=kwargs.get("cancelled", lambda: False))


def render(result, **changes):
    dataset = result["dataset"]
    params = {"bbox": dataset["bounds"], "width": dataset["raster"]["width"], "height": dataset["raster"]["height"], "style": rasters.default_style(dataset)}
    params.update(changes)
    response = rasters.render(dataset, Path(result["artifactPath"]), params)
    raw = base64.b64decode(response["imageBase64"])
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", rasterio.errors.NotGeoreferencedWarning)
        with MemoryFile(raw) as memory:
            with memory.open() as image:
                pixels = image.read()
    return response, pixels


def sample(result, x, y):
    coordinate = Transformer.from_crs(3857, 4326, always_xy=True).transform(x, y)
    return rasters.sample(result["dataset"], Path(result["artifactPath"]), {"coordinate": list(coordinate)})


def make_raster(path, data, *, crs="EPSG:3857", transform=None, **kwargs):
    if data.ndim == 2:
        data = data[None, :, :]
    with rasterio.open(path, "w", driver="GTiff", width=data.shape[2], height=data.shape[1], count=data.shape[0], dtype=data.dtype,
                       crs=crs, transform=transform or from_origin(0, data.shape[1] * 1000, 1000, 1000), **kwargs) as source:
        source.write(data)
    return path


def test_metadata_and_byte_exact_import_export(sources, tmp_path):
    original = Path(sources["dem"]).read_bytes()
    inspected = rasters.inspect_raster({"sourcePath": sources["dem"]})
    band = inspected["raster"]["bands"][0]
    assert (band["dtype"], band["scale"], band["offset"], band["unit"], band["noData"]) == ("float32", 2, 10, "m", -9999)
    assert (band["sampleMin"], band["sampleMax"], band["sampledPixels"], band["validSamplePixels"]) == (0, 23, 24, 21)
    assert inspected["raster"]["horizontalUnit"] == "metre"
    assert inspected["raster"]["verticalCrsWkt"] is None
    assert inspected["raster"]["resolution"] == [1000, 1000]
    result = import_raster(sources["dem"], tmp_path / "work")
    dataset = result["dataset"]
    assert dataset["kind"] == "raster" and dataset["version"] == hashlib.sha256(original).hexdigest()
    assert dataset["relativePath"].endswith(".tif")
    assert Path(result["artifactPath"]).read_bytes() == original
    rasters.verify_snapshot(Path(result["artifactPath"]), dataset)
    exported = rasters.export_raster({"dataset": dataset, "managedPath": result["artifactPath"]}, tmp_path / "export", progress=lambda *args: None, cancelled=lambda: False)
    assert Path(exported["artifactPath"]).read_bytes() == original
    assert Path(sources["dem"]).read_bytes() == original
    json.dumps(dataset, allow_nan=False)


def test_raw_pixel_query_zero_nodata_nonfinite_scale_and_outside(sources, tmp_path):
    result = import_raster(sources["dem"], tmp_path / "work")
    zero = sample(result, 500, 3500)
    assert zero["inside"] and zero["pixel"] == {"row": 0, "column": 0}
    assert zero["bands"] == [{"index": 1, "rawValue": "0.0", "value": 10, "valid": True, "reason": None}]
    nodata = sample(result, 1500, 2500)["bands"][0]
    assert nodata["rawValue"] == "-9999.0" and nodata["value"] is None and not nodata["valid"]
    assert sample(result, 2500, 1500)["bands"][0]["rawValue"] == "NaN"
    assert sample(result, 3500, 1500)["bands"][0]["rawValue"] == "Infinity"
    assert not sample(result, 3500, 1500)["bands"][0]["valid"]
    outside = sample(result, -500, 3500)
    assert not outside["inside"] and outside["pixel"] is None and outside["bands"][0]["reason"] == "outside"


def test_gray_render_uses_raw_ranges_and_transparent_invalid_pixels(sources, tmp_path):
    result = import_raster(sources["dem"], tmp_path / "work")
    response, pixels = render(result)
    assert response["bbox"] == [0, 0, 6000, 4000] and response["dataCrs"] == "EPSG:3857"
    assert pixels.shape == (4, 4, 6)
    assert pixels[:, 0, 0].tolist() == [0, 0, 0, 255]
    assert pixels[:, 3, 5].tolist() == [255, 255, 255, 255]
    assert pixels[3, 1, 1] == 0 and pixels[3, 2, 2] == 0 and pixels[3, 2, 3] == 0
    assert pixels[0, 0, 1] == round(255 / 23)


def test_rgb_order_and_partial_alpha_preserved(sources, tmp_path):
    result = import_raster(sources["rgb"], tmp_path / "work")
    assert rasters.default_style(result["dataset"])["bands"] == [1, 2, 3]
    style = {"mode": "rgb", "bands": [3, 2, 1], "ranges": [[0, 255]] * 3, "resampling": "nearest"}
    _, pixels = render(result, style=style)
    assert pixels[:, 0, 0].tolist() == [50, 100, 200, 255]
    assert pixels[3, 1, 1] == 0 and pixels[3, 2, 2] == 128


def test_internal_mask_and_transparent_space_outside_raster(sources, tmp_path):
    result = import_raster(sources["masked"], tmp_path / "work")
    _, pixels = render(result)
    assert pixels[3, 1, 1] == 0 and pixels[3, 0, 0] == 255
    assert sample(result, 1500, 2500)["bands"][0]["reason"] == "nodata_or_masked"
    _, expanded = render(result, bbox=[-1000, -1000, 7000, 5000], width=8, height=6)
    assert np.all(expanded[3, 0] == 0) and np.all(expanded[3, -1] == 0)
    assert np.all(expanded[3, :, 0] == 0) and np.all(expanded[3, :, -1] == 0)


def test_geographic_raster_reprojects_into_exact_mercator_extent(tmp_path):
    source = make_raster(tmp_path / "geo.tif", np.array([[1, 2], [3, 4]], dtype="uint8"), crs="EPSG:4326", transform=from_origin(114, 31, 0.01, 0.01))
    result = import_raster(source, tmp_path / "work")
    bounds = list(Transformer.from_crs(4326, 3857, always_xy=True).transform_bounds(114, 30.98, 114.02, 31))
    response, pixels = render(result, bbox=bounds, width=2, height=2)
    assert response["bbox"] == bounds and pixels[0].tolist() == [[0, 85], [170, 255]]
    queried = rasters.sample(result["dataset"], Path(result["artifactPath"]), {"coordinate": [114.015, 30.985]})
    assert queried["pixel"] == {"row": 1, "column": 1} and queried["bands"][0]["rawValue"] == "4"


def test_rotated_affine_pixel_query_and_resolution(tmp_path):
    transform = Affine(1000, 200, 0, 100, -1500, 5000)
    source = make_raster(tmp_path / "rotate.tif", np.arange(12, dtype="int16").reshape(3, 4), transform=transform)
    result = import_raster(source, tmp_path / "work")
    xy = transform * (2.5, 1.5)
    response = sample(result, *xy)
    assert response["pixel"] == {"row": 1, "column": 2} and response["bands"][0]["rawValue"] == "6"
    assert result["dataset"]["raster"]["resolution"] == pytest.approx([np.hypot(1000, 100), np.hypot(200, -1500)])


def test_unknown_crs_can_roundtrip_but_map_calls_are_restricted(tmp_path):
    source = make_raster(tmp_path / "unknown.tif", np.ones((2, 2), dtype="uint8"), crs=None)
    result = import_raster(source, tmp_path / "work")
    assert result["dataset"]["crsWkt"] is None and result["dataset"]["report"]["status"] == "restricted"
    for call in (lambda: render(result), lambda: sample(result, 500, 500)):
        with pytest.raises(DomainError) as caught:
            call()
        assert caught.value.kind == "crs_required"


def test_missing_affine_georeferencing_is_not_invented(tmp_path):
    source = tmp_path / "unlocated.tif"
    with pytest.warns(rasterio.errors.NotGeoreferencedWarning):
        with rasterio.open(source, "w", driver="GTiff", width=2, height=2, count=1, dtype="uint8") as image:
            image.write(np.ones((1, 2, 2), dtype="uint8"))
    with pytest.raises(DomainError) as caught:
        rasters.inspect_raster({"sourcePath": str(source)})
    assert caught.value.kind == "unsupported_georeferencing"


@pytest.mark.parametrize("suffix", [".tif.aux.xml", ".tif.msk", ".tif.ovr", ".tfw", ".tifw", ".prj"])
def test_sidecars_are_explicitly_rejected(tmp_path, suffix):
    source = make_raster(tmp_path / "sidecar.tif", np.ones((2, 2), dtype="uint8"))
    (tmp_path / ("sidecar" + suffix)).write_text("external", encoding="ascii")
    with pytest.raises(DomainError) as caught:
        rasters.inspect_raster({"sourcePath": str(source)})
    assert caught.value.kind == "external_raster_dependencies"


def test_nan_nodata_and_empty_sample_range_are_json_safe(tmp_path):
    source = make_raster(tmp_path / "nan.tif", np.full((2, 3), np.nan, dtype="float32"), nodata=np.nan)
    result = import_raster(source, tmp_path / "work")
    assert result["dataset"]["raster"]["bands"][0]["noData"] == "NaN"
    assert rasters.default_style(result["dataset"])["ranges"] == [[0, 1]]
    assert "no finite valid" in " ".join(result["dataset"]["report"]["warnings"])
    _, pixels = render(result)
    assert np.all(pixels[3] == 0)
    json.dumps(result, allow_nan=False)


def test_64_bit_integer_query_retains_exact_raw_string(tmp_path):
    source = make_raster(tmp_path / "integer.tif", np.array([[9007199254740993]], dtype="uint64"))
    result = import_raster(source, tmp_path / "work")
    band = sample(result, 500, 500)["bands"][0]
    assert band == {"index": 1, "rawValue": "9007199254740993", "value": None, "valid": True, "reason": "scaled_value_precision_unavailable"}


def test_unsafe_64_bit_integer_nodata_is_not_silently_rounded(tmp_path):
    source = make_raster(tmp_path / "integer-nodata.tif", np.array([[9007199254740993]], dtype="uint64"), nodata=9007199254740993)
    with pytest.raises(DomainError) as caught:
        rasters.inspect_raster({"sourcePath": str(source)})
    assert caught.value.kind == "unsupported_raster_nodata"


def test_constant_band_range_produces_midgray(tmp_path):
    source = make_raster(tmp_path / "constant.tif", np.full((2, 2), 7, dtype="uint8"))
    result = import_raster(source, tmp_path / "work")
    _, pixels = render(result)
    assert np.all(pixels[:3] == 127) and np.all(pixels[3] == 255)


def test_uint16_alpha_normalizes_to_png_byte_range(tmp_path):
    source = make_raster(tmp_path / "alpha16.tif", np.array([[[1000, 1000]], [[2000, 2000]], [[3000, 3000]], [[32768, 65535]]], dtype="uint16"))
    with rasterio.open(source, "r+") as image:
        image.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha)
    result = import_raster(source, tmp_path / "work")
    _, pixels = render(result)
    assert pixels[3].tolist() == [[128, 255]]


def test_explicit_nodata_remains_invalid_when_internal_mask_marks_it_valid(tmp_path):
    source = make_raster(tmp_path / "mask-nodata.tif", np.array([[0, -9999, 5, 10]], dtype="int16"), nodata=-9999)
    with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
        with rasterio.open(source, "r+") as image:
            image.write_mask(np.array([[255, 255, 255, 0]], dtype="uint8"))
    result = import_raster(source, tmp_path / "work")
    band = result["dataset"]["raster"]["bands"][0]
    assert band["validSamplePixels"] == 2 and band["sampleMin"] == 0
    assert not sample(result, 1500, 500)["bands"][0]["valid"]
    assert sample(result, 500, 500)["bands"][0]["valid"]
    assert not sample(result, 3500, 500)["bands"][0]["valid"]
    _, pixels = render(result)
    assert pixels[3].tolist() == [[255, 0, 255, 0]]


def test_internal_overviews_preserved_but_full_base_blocks_are_scanned(tmp_path):
    source = make_raster(tmp_path / "overview.tif", np.arange(1024, dtype="uint16").reshape(32, 32), tiled=True, blockxsize=16, blockysize=16)
    with rasterio.open(source, "r+") as image:
        image.build_overviews([2, 4], Resampling.nearest)
        image.update_tags(ns="rio_overview", resampling="nearest")
    progress = []
    result = import_raster(source, tmp_path / "work", progress=lambda *args: progress.append(args))
    assert result["dataset"]["raster"]["bands"][0]["overviews"] == [2, 4]
    assert result["dataset"]["raster"]["tagNamespaces"]["rio_overview"] == {"resampling": "nearest"}
    assert ("validating", 4, 4) in progress


def test_average_overviews_do_not_introduce_new_category_values(tmp_path):
    values = np.where(np.indices((512, 512)).sum(axis=0) % 2, 10, 1).astype("uint8")
    source = make_raster(tmp_path / "categories.tif", values, tiled=True, blockxsize=64, blockysize=64)
    with rasterio.open(source, "r+") as image:
        image.build_overviews([2, 4], Resampling.average)
    result = import_raster(source, tmp_path / "work")
    band = result["dataset"]["raster"]["bands"][0]
    assert band["sampledPixels"] == 256 * 256
    assert band["sampleMin"] in (1, 10) and band["sampleMax"] in (1, 10)
    style = {"mode": "gray", "bands": [1], "ranges": [[1, 10]], "resampling": "nearest"}
    _, pixels = render(result, width=128, height=128, style=style)
    assert set(np.unique(pixels[0])).issubset({0, 255})


def test_source_change_during_copy_fails_import(sources, tmp_path):
    changed = False

    def progress(stage, *_):
        nonlocal changed
        if stage == "copying" and not changed:
            with Path(sources["dem"]).open("ab") as stream:
                stream.write(b"changed")
            changed = True

    with pytest.raises(DomainError) as caught:
        import_raster(sources["dem"], tmp_path / "work", progress=progress)
    assert caught.value.kind == "source_changed"


@pytest.mark.parametrize("limit", ["MAX_DECODED_BYTES", "MAX_BLOCK_BYTES", "MAX_DIMENSION", "MAX_BANDS"])
def test_decoded_layout_budgets_are_enforced(sources, monkeypatch, limit):
    monkeypatch.setattr(rasters, limit, 0)
    with pytest.raises(DomainError) as caught:
        rasters.inspect_raster({"sourcePath": sources["dem"]})
    assert caught.value.kind == "source_limit"


@pytest.mark.parametrize("dimensions,bands,block", [(8192, 1, 8192), (16384, 16, 256)])
def test_sparse_tiff_excessive_block_or_decoded_size_rejected_before_pixel_reads(tmp_path, monkeypatch, dimensions, bands, block):
    source = tmp_path / "oversize.tif"
    with rasterio.open(source, "w", driver="GTiff", width=dimensions, height=dimensions, count=bands, dtype="uint8",
                       crs="EPSG:3857", transform=from_origin(0, dimensions, 1, 1), tiled=True, blockxsize=block, blockysize=block,
                       SPARSE_OK=True, BIGTIFF="YES"):
        pass
    assert source.stat().st_size < rasters.MAX_SOURCE_BYTES

    def unexpected_metadata(*_):
        pytest.fail("Inspection attempted pixel sampling before layout budget validation")

    monkeypatch.setattr(rasters, "_metadata", unexpected_metadata)
    with pytest.raises(DomainError) as caught:
        rasters.inspect_raster({"sourcePath": str(source)})
    assert caught.value.kind == "source_limit"


@pytest.mark.parametrize("changes", [{"width": 1025}, {"height": True}, {"bbox": [0, 0, 0, 1]}, {"bbox": [0, 0, 1, float("inf")]},
                                    {"bbox": [0, 0, 30000000, 1]}, {"style": {"mode": "gray", "bands": [0], "ranges": [[0, 1]], "resampling": "nearest"}},
                                    {"style": {"mode": "gray", "bands": [1], "ranges": [[2, 1]], "resampling": "nearest"}},
                                    {"style": {"mode": "gray", "bands": [1], "ranges": [[0, 1]], "resampling": "bilinear"}}])
def test_render_parameters_are_strictly_bounded(sources, tmp_path, changes):
    result = import_raster(sources["dem"], tmp_path / "work")
    with pytest.raises(InvalidParamsError):
        render(result, **changes)


def test_tampered_snapshot_and_metadata_are_rejected(sources, tmp_path):
    result = import_raster(sources["dem"], tmp_path / "work")
    result["dataset"]["raster"]["bands"][0]["scale"] = 99
    with pytest.raises(DomainError) as metadata:
        rasters.verify_snapshot(Path(result["artifactPath"]), result["dataset"])
    assert metadata.value.kind == "roundtrip_failed"
    with Path(result["artifactPath"]).open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(DomainError) as content:
        rasters.verify_snapshot(Path(result["artifactPath"]), result["dataset"])
    assert content.value.kind == "snapshot_changed"


@pytest.mark.parametrize("query", ["render", "sample"])
def test_queries_reject_snapshot_tampering_after_cached_validation(sources, tmp_path, query):
    result = import_raster(sources["dem"], tmp_path / "work")
    call = (lambda: render(result)) if query == "render" else (lambda: sample(result, 500, 3500))
    call()
    with rasterio.open(result["artifactPath"], "r+") as image:
        image.write(np.full((4, 6), 42, dtype="float32"), 1)
    with pytest.raises(DomainError) as caught:
        call()
    assert caught.value.kind == "snapshot_changed"


def test_integrity_cache_avoids_rehash_and_rechecks_metadata_and_sidecars(sources, tmp_path, monkeypatch):
    result = import_raster(sources["dem"], tmp_path / "work")
    original_hash = rasters._content_hash
    calls = []

    def counting_hash(*args, **kwargs):
        calls.append(args[0])
        return original_hash(*args, **kwargs)

    monkeypatch.setattr(rasters, "_content_hash", counting_hash)
    sample(result, 500, 3500)
    sample(result, 1500, 3500)
    render(result)
    assert len(calls) == 1
    result["dataset"]["raster"]["bands"][0]["scale"] = 99
    with pytest.raises(DomainError) as changed_metadata:
        sample(result, 500, 3500)
    assert changed_metadata.value.kind == "roundtrip_failed" and len(calls) == 2
    result["dataset"]["raster"]["bands"][0]["scale"] = 2
    Path(result["artifactPath"] + ".aux.xml").write_text("sidecar", encoding="ascii")
    with pytest.raises(DomainError) as sidecar:
        sample(result, 500, 3500)
    assert sidecar.value.kind == "external_raster_dependencies"


def test_integrity_cache_is_bounded(sources, tmp_path, monkeypatch):
    monkeypatch.setattr(rasters, "MAX_INTEGRITY_CACHE", 2)
    for index in range(3):
        result = import_raster(sources["dem"], tmp_path / str(index))
        sample(result, 500, 3500)
    assert len(rasters._INTEGRITY_CACHE) == 2


def test_source_byte_and_metadata_budgets(sources, monkeypatch):
    monkeypatch.setattr(rasters, "MAX_SOURCE_BYTES", 1)
    with pytest.raises(DomainError) as caught:
        rasters.inspect_raster({"sourcePath": sources["dem"]})
    assert caught.value.kind == "source_limit"
    monkeypatch.setattr(rasters, "MAX_SOURCE_BYTES", 512 * 1024 * 1024)
    monkeypatch.setattr(rasters, "MAX_METADATA_BYTES", 10)
    with pytest.raises(DomainError) as caught:
        rasters.inspect_raster({"sourcePath": sources["dem"]})
    assert caught.value.kind == "source_limit"


def test_corrupt_and_complex_sources_rejected(tmp_path):
    corrupt = tmp_path / "corrupt.tif"
    corrupt.write_bytes(b"not a TIFF")
    with pytest.raises(DomainError):
        rasters.inspect_raster({"sourcePath": str(corrupt)})
    complex_source = make_raster(tmp_path / "complex.tif", np.ones((2, 2), dtype="complex64"))
    with pytest.raises(DomainError) as caught:
        rasters.inspect_raster({"sourcePath": str(complex_source)})
    assert caught.value.kind == "unsupported_raster_type"


def test_cancellation_and_existing_snapshot_do_not_overwrite(sources, tmp_path):
    with pytest.raises(DomainError) as caught:
        import_raster(sources["dem"], tmp_path / "cancelled", cancelled=lambda: True)
    assert caught.value.kind == "task_cancelled" and not (tmp_path / "cancelled" / "snapshot.tif").exists()
    work = tmp_path / "existing"
    work.mkdir()
    (work / "snapshot.tif").write_bytes(b"existing")
    with pytest.raises(DomainError) as caught:
        import_raster(sources["dem"], work)
    assert caught.value.kind == "destination_exists" and (work / "snapshot.tif").read_bytes() == b"existing"


def test_mid_copy_cancellation_removes_owned_staging_without_publishing(tmp_path):
    source = make_raster(
        tmp_path / "cancel-copy.tif", np.ones((1024, 2048), dtype="uint8"),
        tiled=True, blockxsize=512, blockysize=512,
    )
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    projects = ProjectStore()
    project = projects.create({"directory": str(tmp_path / "project"), "name": "Cancel raster copy"})
    workspace = WorkspaceStore(projects)

    def command(request_path):
        # Pause the real worker only after its first copy progress is on disk.
        script = """
import sys
import time
from pathlib import Path
from spatial_engine import worker

request_path = Path(sys.argv[1])
original_atomic_json = worker._atomic_json

def report_and_pause(path, value, maximum):
    original_atomic_json(path, value, maximum)
    if path.name == "progress.json" and value["stage"] == "copying" and 0 < value["completed"] < value["total"]:
        deadline = time.monotonic() + 20
        while not (request_path.parent / "cancel.flag").is_file():
            if time.monotonic() >= deadline:
                raise RuntimeError("Mid-copy cancellation was not requested")
            time.sleep(0.01)

worker._atomic_json = report_and_pause
sys.exit(worker.run_worker(request_path))
"""
        return [sys.executable, "-c", script, str(request_path)]

    manager = TaskManager(projects, workspace, command_factory=command)
    try:
        task = manager.start_raster_import({"path": project["projectPath"], "sourcePath": str(source)})
        params = {"path": project["projectPath"], "taskId": task["id"]}
        root = Path(project["projectPath"]).parent
        work_dir = root / "staging" / "tasks" / task["id"]
        deadline = time.monotonic() + 15
        while task["stage"] != "copying" and task["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.02)
            task = manager.get(params)
        assert task["stage"] == "copying", task
        assert 0 < task["completed"] < task["total"] == source.stat().st_size
        assert (work_dir / "snapshot.tif").stat().st_size == task["completed"]
        assert not (work_dir / "result.json").exists()
        process = manager._process
        assert process is not None and process.poll() is None

        cancelled = manager.cancel(params)

        assert cancelled["status"] == "cancelled"
        assert process.poll() is not None and manager._process is None
        assert not work_dir.exists()
        assert not (root / "rasters" / f"{task['datasetId']}.tif").exists()
        assert not workspace.has_pending(task["id"])
        snapshot = workspace.get({"path": project["projectPath"]})
        assert snapshot["datasets"] == [] and snapshot["layers"] == []
        assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    finally:
        manager.close()
        projects.close()
