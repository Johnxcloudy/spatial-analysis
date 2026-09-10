from __future__ import annotations

import csv
import importlib
import sqlite3
import uuid
from pathlib import Path

import geopandas as gpd
import pyogrio
import pytest
from shapely.geometry import Polygon, box

from spatial_engine.errors import DomainError
from spatial_engine.vectors import import_vector


def module():
    assert importlib.util.find_spec("spatial_engine.analysis"), "Phase 2 analysis module is not implemented"
    return importlib.import_module("spatial_engine.analysis")


def source(tmp_path, name, geometries, codes=None, crs="EPSG:32650"):
    path = tmp_path / f"{name}.gpkg"
    frame = gpd.GeoDataFrame({"code": codes if codes is not None else ["boundary"] * len(geometries)}, geometry=geometries, crs=crs)
    pyogrio.write_dataframe(frame, path, layer="land")
    return import_vector({"sourcePath": str(path), "sourceLayer": "land", "encoding": None,
                          "assignedCrs": None, "datasetId": str(uuid.uuid4())}, tmp_path / name, lambda *a: None, lambda: False)


def rect(x, y, width, height):
    return box(500000 + x, 3000000 + y, 500000 + x + width, 3000000 + y + height)


def run(tmp_path, left, right, operation="clip", **options):
    args = {"operation": operation, "name": "Analytic result", "inputDatasetId": left["dataset"]["id"],
            "overlayDatasetId": right["dataset"]["id"], "inputClassField": "code",
            "overlayClassField": "code" if operation == "intersect" else None,
            "classificationStandard": "Synthetic fixture v1", "analysisCrs": "EPSG:32650",
            "crsReason": "Synthetic parcels within UTM zone 50N"}
    args.update(options)
    return module().run_analysis({"taskId": str(uuid.uuid4()), "datasetId": str(uuid.uuid4()),
                                 "analysisId": str(uuid.uuid4()), "input": left["dataset"], "overlay": right["dataset"],
                                 "inputPath": left["artifactPath"], "overlayPath": right["artifactPath"], "options": args},
                                tmp_path / "analysis", lambda *a: None, lambda: False)


def page(result):
    return module().result_page(result["dataset"], Path(result["artifactPath"]), {"offset": 0, "limit": 100})


def test_clip_boundary_union_area_classes_csv_and_registered_tables(tmp_path):
    left = source(tmp_path, "left", [rect(i * 10, 0, 10, 10) for i in range(5)], ["001", None, "NULL", "", " "])
    right = source(tmp_path, "right", [rect(0, 0, 40, 20), rect(30, 0, 30, 20)])
    result = run(tmp_path, left, right)
    value = page(result)
    assert value["record"]["studyAreaM2"] == pytest.approx(1200)
    assert value["record"]["coveredAreaM2"] == pytest.approx(500)
    assert value["record"]["uncoveredAreaM2"] == pytest.approx(700)
    assert {row["inputClass"] for row in value["rows"]} == {"001", None, "NULL", "", " "}
    assert all(row["areaM2"] == 100 and row["coverageRatio"] == .2 for row in value["rows"])
    with sqlite3.connect(result["artifactPath"]) as db:
        names = dict(db.execute("SELECT table_name,data_type FROM gpkg_contents"))
        assert names["analysis_statistics"] == "attributes"
        assert names["analysis_record"] == "attributes"
    exported = module().export_statistics({"dataset": result["dataset"], "managedPath": result["artifactPath"]},
                                         tmp_path / "csv", lambda *a: None, lambda: False)
    with open(exported["artifactPath"], encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 5
    assert any(row["inputClass"] == "001" for row in rows)
    assert sum(row["inputClassIsNull"] == "true" for row in rows) == 1


@pytest.mark.parametrize("operation", ["clip", "intersect"])
def test_positive_overlap_is_rejected_even_for_same_class(tmp_path, operation):
    left = source(tmp_path, "left", [rect(0, 0, 10, 10), rect(5, 0, 10, 10)], ["001", "001"])
    right = source(tmp_path, "right", [rect(0, 0, 20, 20)])
    with pytest.raises(DomainError, match="overlap"):
        run(tmp_path, left, right, operation)


def test_intersection_pair_classes_and_hole(tmp_path):
    outer = rect(0, 0, 20, 20)
    hole = rect(5, 5, 10, 10)
    left = source(tmp_path, "left", [Polygon(outer.exterior.coords, [hole.exterior.coords])], ["001"])
    right = source(tmp_path, "right", [rect(0, 0, 10, 20), rect(10, 0, 10, 20)], ["A", "B"])
    value = page(run(tmp_path, left, right, "intersect"))
    assert value["record"]["studyAreaM2"] == 400
    assert value["record"]["coveredAreaM2"] == 300
    assert {(r["inputClass"], r["overlayClass"], r["areaM2"]) for r in value["rows"]} == {("001", "A", 150), ("001", "B", 150)}


def test_empty_and_boundary_contact_do_not_create_fake_polygons(tmp_path):
    left = source(tmp_path, "left", [rect(0, 0, 10, 10)], ["001"])
    right = source(tmp_path, "right", [rect(10, 0, 10, 10)])
    result = run(tmp_path, left, right)
    value = page(result)
    assert value["rows"] == []
    assert value["record"]["coveredAreaM2"] == 0
    assert value["record"]["boundaryContactCount"] == 1
    assert pyogrio.read_info(result["artifactPath"], layer="features")["features"] == 0


@pytest.mark.parametrize("crs", ["EPSG:4326", "EPSG:3857", "EPSG:4978"])
def test_unsuitable_analysis_crs_rejected(tmp_path, crs):
    left = source(tmp_path, "left", [rect(0, 0, 10, 10)], ["001"])
    right = source(tmp_path, "right", [rect(0, 0, 20, 20)])
    with pytest.raises(DomainError, match="CRS"):
        run(tmp_path, left, right, analysisCrs=crs)


def test_source_hash_change_is_rejected(tmp_path):
    left = source(tmp_path, "left", [rect(0, 0, 10, 10)], ["001"])
    right = source(tmp_path, "right", [rect(0, 0, 20, 20)])
    with open(left["artifactPath"], "ab") as stream:
        stream.write(b"changed")
    with pytest.raises(DomainError, match="changed"):
        run(tmp_path, left, right)


def test_output_metadata_is_accepted_by_workspace(tmp_path):
    from spatial_engine.workspace import _validate_dataset
    left = source(tmp_path, "left", [rect(0, 0, 10, 10)], ["001"])
    right = source(tmp_path, "right", [rect(0, 0, 20, 20)])
    result = run(tmp_path, left, right)
    _validate_dataset(result["dataset"])
    from spatial_engine.vectors import export_vector
    exported = export_vector({"dataset": result["dataset"], "managedPath": result["artifactPath"]},
                             tmp_path / "exported", lambda *a: None, lambda: False)
    assert Path(exported["artifactPath"]).read_bytes() == Path(result["artifactPath"]).read_bytes()


def test_long_classification_fails_instead_of_unbounded_page(tmp_path):
    left = source(tmp_path, "left", [rect(0, 0, 10, 10)], ["a" * 1025])
    right = source(tmp_path, "right", [rect(0, 0, 20, 20)])
    with pytest.raises(DomainError, match="Classification value"):
        run(tmp_path, left, right)


def test_mixed_crs_records_real_operation_and_preserves_area(tmp_path):
    left = source(tmp_path, "left", [rect(0, 0, 10, 10)], ["001"])
    boundary = gpd.GeoSeries([rect(-1, -1, 12, 12)], crs=32650).to_crs(4326)
    right = source(tmp_path, "right", list(boundary), crs="EPSG:4326")
    value = page(run(tmp_path, left, right))
    assert value["record"]["coveredAreaM2"] == pytest.approx(100, abs=1e-6)
    assert "+proj=utm" in value["record"]["transforms"][1]["operation"] or "proj=utm" in value["record"]["transforms"][1]["operation"]


def test_us_survey_foot_area_converts_to_square_metres(tmp_path):
    from pyproj import CRS
    left = source(tmp_path, "left", [box(1000000, 200000, 1000010, 200010)], ["001"], crs="EPSG:2263")
    right = source(tmp_path, "right", [box(999999, 199999, 1000011, 200011)], crs="EPSG:2263")
    value = page(run(tmp_path, left, right, analysisCrs="EPSG:2263", crsReason="New York Long Island synthetic fixture"))
    expected = 100 * CRS.from_epsg(2263).axis_info[0].unit_conversion_factor ** 2
    assert value["record"]["coveredAreaM2"] == pytest.approx(expected, abs=1e-10)


@pytest.mark.parametrize("budget", ["MAX_CANDIDATES", "MAX_FEATURES", "MAX_VERTICES", "MAX_BYTES", "MAX_CLASSES"])
def test_resource_limits_stop_without_success(tmp_path, monkeypatch, budget):
    left = source(tmp_path, "left", [rect(0, 0, 10, 10)], ["001"])
    right = source(tmp_path, "right", [rect(0, 0, 20, 20)])
    monkeypatch.setattr(module(), budget, 0)
    with pytest.raises(DomainError) as exc:
        run(tmp_path, left, right)
    assert exc.value.kind == "analysis_limit"


def test_intersection_rejects_overlay_duplicates(tmp_path):
    left = source(tmp_path, "left", [rect(0, 0, 10, 10)], ["001"])
    right = source(tmp_path, "right", [rect(0, 0, 20, 20), rect(0, 0, 20, 20)])
    with pytest.raises(DomainError, match="overlap"):
        run(tmp_path, left, right, "intersect")


def test_invalid_polygon_rejected_without_repair(tmp_path):
    left = source(tmp_path, "left", [Polygon([(500000, 3000000), (500010, 3000010), (500010, 3000000), (500000, 3000010), (500000, 3000000)])], ["001"])
    right = source(tmp_path, "right", [rect(0, 0, 20, 20)])
    with pytest.raises(DomainError, match="invalid"):
        run(tmp_path, left, right)


def test_result_roundtrip_detects_classification_corruption(tmp_path, monkeypatch):
    left = source(tmp_path, "left", [rect(0, 0, 10, 10)], ["001"])
    right = source(tmp_path, "right", [rect(0, 0, 20, 20)])
    original = module()._write_batch
    def corrupt(path, rows, crs, first):
        original(path, [{**row, "input_class": "corrupt"} for row in rows], crs, first)
    monkeypatch.setattr(module(), "_write_batch", corrupt)
    with pytest.raises(DomainError, match="roundtrip"):
        run(tmp_path, left, right)


def test_cancel_during_overlap_checks_does_not_publish_result(tmp_path, monkeypatch):
    left = source(tmp_path, "left", [rect(0, 0, 10, 10)], ["001"])
    right = source(tmp_path, "right", [rect(0, 0, 20, 20)])
    original = module()._check_overlaps
    def cancel(geometries, ids, tree, budget, progress, cancelled):
        return original(geometries, ids, tree, budget, progress, lambda: True)
    monkeypatch.setattr(module(), "_check_overlaps", cancel)
    with pytest.raises(DomainError) as exc:
        run(tmp_path, left, right)
    assert exc.value.kind == "task_cancelled"
    assert not (tmp_path / "analysis" / "snapshot.gpkg").exists()


def test_intersection_output_explosion_hits_output_budget(tmp_path, monkeypatch):
    left = source(tmp_path, "left", [rect(0, 0, 10, 20), rect(10, 0, 10, 20)], ["001", "002"])
    right = source(tmp_path, "right", [rect(0, 0, 20, 10), rect(0, 10, 20, 10)], ["A", "B"])
    monkeypatch.setattr(module(), "MAX_FEATURES", 3)
    with pytest.raises(DomainError, match="output geometry budget"):
        run(tmp_path, left, right, "intersect")


def test_missing_best_transform_grid_never_falls_back(tmp_path, monkeypatch):
    from types import SimpleNamespace
    left = source(tmp_path, "left", [rect(0, 0, 10, 10)], ["001"])
    right = source(tmp_path, "right", [rect(0, 0, 20, 20)])
    monkeypatch.setattr(module(), "TransformerGroup", lambda *a, **kw: SimpleNamespace(best_available=False, transformers=[object()]))
    with pytest.raises(DomainError, match="ballpark fallback is disabled"):
        run(tmp_path, left, right)


def test_invalid_operation_is_parameter_error(tmp_path):
    from spatial_engine.errors import InvalidParamsError
    left = source(tmp_path, "left", [rect(0, 0, 10, 10)], ["001"])
    right = source(tmp_path, "right", [rect(0, 0, 20, 20)])
    with pytest.raises(InvalidParamsError):
        run(tmp_path, left, right, operation=[])
