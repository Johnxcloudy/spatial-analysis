from __future__ import annotations

import importlib
import json
import sqlite3
import struct
import time
import uuid
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
import pyarrow as pa
import pytest
import shapely
from shapely.geometry import Point, Polygon

from spatial_engine.errors import DomainError
from spatial_engine.projects import ProjectStore
from spatial_engine.tasks import TaskManager
from spatial_engine.workspace import WorkspaceStore
from vector_fixtures import create_fixture_bundle, ordinary_frame


def vectors():
    return importlib.import_module("spatial_engine.vectors")


def import_path(path: Path | str, work_dir: Path, layer="land", **overrides):
    payload = {"sourcePath": str(path), "sourceLayer": layer, "encoding": None,
               "assignedCrs": None, "datasetId": str(uuid.uuid4())}
    payload.update(overrides)
    return vectors().import_vector(payload, work_dir, lambda *args: None, lambda: False)


def test_vector_module_is_available():
    assert importlib.util.find_spec("spatial_engine.vectors") is not None


@pytest.fixture
def sources(tmp_path):
    return create_fixture_bundle(tmp_path / "sources")


@pytest.mark.parametrize("kind", ["gpkg", "shp", "geojson", "gdb"])
def test_real_format_import_and_verified_export(sources, tmp_path, kind):
    source = sources[kind]
    inspected = vectors().inspect_source({"sourcePath": source, "encoding": "GBK" if kind == "shp" else None})
    assert any(layer["name"] == "land" for layer in inspected["layers"])
    result = import_path(source, tmp_path / "work", encoding="GBK" if kind == "shp" else None)
    dataset = result["dataset"]
    assert dataset["featureCount"] == 4
    assert dataset["source"]["fingerprint"]
    assert dataset["storageLayer"] == "features"
    table = pyogrio.read_arrow(result["artifactPath"], layer="features")[1]
    assert table["code"].to_pylist() == ["001", "002", "003", "004"]
    assert table["label"].to_pylist()[0] == "Chinese \u571f\u5730"
    if kind != "shp":
        assert table["large_id"].to_pylist() == [9007199254740993, None, 9007199254740995, 4]
    assert len(set(table[dataset["internalIdField"]].to_pylist())) == 4
    exported = vectors().export_vector({"dataset": dataset, "managedPath": result["artifactPath"]},
                                       tmp_path / "export", lambda *args: None, lambda: False)
    assert Path(exported["artifactPath"]).read_bytes() == Path(result["artifactPath"]).read_bytes()


def test_gdb_and_gpkg_inspect_multiple_layers(sources):
    for kind in ("gdb", "gpkg"):
        result = vectors().inspect_source({"sourcePath": sources[kind], "encoding": None})
        assert {layer["name"] for layer in result["layers"]} == {"land", "controls"}


@pytest.mark.parametrize("missing_suffix", [".shx", ".dbf"])
def test_missing_shapefile_component_refused_without_registration(sources, tmp_path, missing_suffix):
    source = Path(sources["shp"])
    missing = source.with_suffix(missing_suffix)
    missing.unlink()
    original_components = {
        path: path.read_bytes() for path in source.parent.glob(f"{source.stem}.*")
        if path.is_file() and path.suffix.lower() in {".shp", ".shx", ".dbf", ".prj", ".cpg"}
    }
    with pytest.raises(DomainError) as inspection_error:
        vectors().inspect_source({"sourcePath": str(source), "encoding": "GBK"})
    assert inspection_error.value.kind == "missing_components"
    assert missing_suffix in inspection_error.value.detail
    direct_work = tmp_path / "direct-work"
    with pytest.raises(DomainError) as import_error:
        import_path(source, direct_work, encoding="GBK")
    assert import_error.value.kind == "missing_components"
    assert not (direct_work / "snapshot.gpkg").exists()

    projects = ProjectStore()
    project = projects.create({"directory": str(tmp_path / "project"), "name": "Missing component"})
    workspace = WorkspaceStore(projects)
    tasks = TaskManager(projects, workspace)
    try:
        task = tasks.start_import({
            "path": project["projectPath"], "sourcePath": str(source), "sourceLayer": "land",
            "encoding": "GBK", "assignedCrs": None,
        })
        deadline = time.monotonic() + 10
        while task["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.02)
            task = tasks.get({"path": project["projectPath"], "taskId": task["id"]})
        assert task["status"] == "failed"
        assert missing_suffix in task["error"]
        state = workspace.get({"path": project["projectPath"]})
        assert state["datasets"] == [] and state["layers"] == []
        assert not workspace.has_pending(task["id"])
        root = Path(project["projectPath"]).parent
        assert list((root / "datasets").iterdir()) == []
        assert not (root / "staging" / "tasks" / task["id"]).exists()
    finally:
        tasks.close()
        projects.close()
    assert not missing.exists()
    assert all(path.read_bytes() == content for path, content in original_components.items())


def test_missing_crs_restricted_and_explicit_assignment(tmp_path):
    path = tmp_path / "unknown.shp"
    frame = gpd.GeoDataFrame({"name": ["unknown"]}, geometry=[Point(114, 27)])
    with pytest.warns(UserWarning, match="crs"):
        pyogrio.write_dataframe(frame, path, driver="ESRI Shapefile")
    unknown = import_path(path, tmp_path / "unknown-work", layer="unknown")["dataset"]
    assert unknown["crsWkt"] is None and unknown["boundsWgs84"] is None
    assert unknown["report"]["status"] == "restricted"
    assigned = import_path(path, tmp_path / "assigned", layer="unknown", assignedCrs="EPSG:4326")["dataset"]
    assert assigned["crsAuthority"] == "EPSG:4326"
    assert assigned["source"]["crsWkt"] is None


def test_reject_existing_crs_override_and_3d(sources, tmp_path):
    with pytest.raises(DomainError, match="CRS"):
        import_path(sources["gpkg"], tmp_path / "override", assignedCrs="EPSG:4326")
    path = tmp_path / "three.gpkg"
    pyogrio.write_dataframe(gpd.GeoDataFrame({"name": ["z"]}, geometry=[Point(1, 2, 3)], crs=4326), path, layer="land")
    with pytest.raises(DomainError) as error:
        import_path(path, tmp_path / "three-work")
    assert error.value.kind == "unsupported_geometry"


def test_invalid_empty_null_preserved_as_restricted(tmp_path):
    path = tmp_path / "invalid.gpkg"
    frame = gpd.GeoDataFrame({"code": ["1", "2", "3"]}, geometry=[Polygon([(0,0),(1,1),(1,0),(0,1),(0,0)]), Polygon(), None], crs=4326)
    pyogrio.write_dataframe(frame, path, layer="land", use_arrow=True)
    result = import_path(path, tmp_path / "invalid-work")
    assert result["dataset"]["featureCount"] == 3
    assert result["dataset"]["report"]["status"] == "restricted"
    assert result["dataset"]["report"]["counts"]["invalid"] == 1
    assert result["dataset"]["report"]["counts"]["empty"] == 1
    assert result["dataset"]["report"]["counts"]["missing"] == 1


def test_sparse_source_fid_and_colliding_internal_names(tmp_path):
    path = tmp_path / "sparse.gpkg"
    frame = ordinary_frame()
    frame["_sa_id"] = "original"
    frame["_sa_source_fid"] = "source"
    frame["fid"] = [10, 20, 30, 40]
    pyogrio.write_dataframe(frame, path, layer="land", use_arrow=True)
    result = import_path(path, tmp_path / "sparse-work")
    dataset = result["dataset"]
    assert dataset["internalIdField"] != "_sa_id"
    table = pyogrio.read_arrow(result["artifactPath"], layer="features")[1]
    assert table[dataset["sourceFidField"]].to_pylist() == ["10", "20", "30", "40"]


def test_import_cancellation_and_changed_source(sources, tmp_path):
    payload = {"sourcePath": sources["gpkg"], "sourceLayer": "land", "encoding": None, "assignedCrs": None, "datasetId": str(uuid.uuid4())}
    with pytest.raises(DomainError) as error:
        vectors().import_vector(payload, tmp_path / "cancel", lambda *args: None, lambda: True)
    assert error.value.kind == "task_cancelled"
    changed = False
    def progress(stage, completed, total):
        nonlocal changed
        if stage == "validating" and not changed:
            with Path(sources["gpkg"]).open("ab") as stream:
                stream.write(b"changed")
            changed = True
    with pytest.raises(DomainError) as error:
        vectors().import_vector(payload, tmp_path / "changed", progress, lambda: False)
    assert error.value.kind == "source_changed"


def test_encoding_provenance_and_snapshot_metadata_checked(sources, tmp_path):
    result = import_path(sources["shp"], tmp_path / "encoding", encoding="GBK")
    dataset = result["dataset"]
    assert dataset["source"]["metadata"]["shapefile.cpg"].upper() == "GBK"
    assert dataset["source"]["metadata"]["encodingSelected"] == "GBK"
    corrupted = dict(dataset, bounds=[0, 0, 1, 1])
    with pytest.raises(DomainError) as error:
        vectors().verify_snapshot(Path(result["artifactPath"]), corrupted)
    assert error.value.kind == "roundtrip_failed"
    corrupted = dict(dataset, fields=[dict(field, storageType="bool") for field in dataset["fields"]])
    with pytest.raises(DomainError):
        vectors().verify_snapshot(Path(result["artifactPath"]), corrupted)


@pytest.mark.parametrize("properties", [{"object": {"number": 1}}, {"array": [1, None, 2]}])
def test_geojson_nested_attributes_rejected_without_silent_string_conversion(tmp_path, properties):
    path = tmp_path / "nested.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [114, 27]}, "properties": properties}
    ]}), encoding="utf-8")
    with pytest.raises(DomainError) as error:
        import_path(path, tmp_path / "nested-work", layer="nested")
    assert error.value.kind == "unsupported_fields"


def test_bool_integer_date_storage_types_and_nulls_preserved(tmp_path):
    path = tmp_path / "typed.gpkg"
    frame = gpd.GeoDataFrame({"flag": pd.array([True, None], dtype="boolean"),
                              "number": pd.array([1, None], dtype="Int64")},
                             geometry=[Point(114, 27), Point(114.1, 27.1)], crs=4326)
    pyogrio.write_dataframe(frame, path, layer="land", use_arrow=True)
    result = import_path(path, tmp_path / "typed-work")
    fields = {field["name"]: field for field in result["dataset"]["fields"]}
    assert fields["flag"]["storageType"] == "bool"
    assert fields["number"]["storageType"] == "int64"


def test_replacement_character_is_not_silently_accepted(tmp_path):
    path = tmp_path / "replacement.gpkg"
    pyogrio.write_dataframe(gpd.GeoDataFrame({"text": ["bad\ufffd"]}, geometry=[Point(114, 27)], crs=4326), path, layer="land", use_arrow=True)
    with pytest.raises(DomainError) as error:
        import_path(path, tmp_path / "replacement-work")
    assert error.value.kind == "encoding_error"


def test_import_record_vertex_and_field_caps(sources, tmp_path, monkeypatch):
    monkeypatch.setattr(vectors(), "MAX_FEATURES", 3)
    with pytest.raises(DomainError) as error:
        import_path(sources["gpkg"], tmp_path / "limited")
    assert error.value.kind == "source_limit"
    monkeypatch.setattr(vectors(), "MAX_FEATURES", 100_000)
    monkeypatch.setattr(vectors(), "MAX_VERTICES", 3)
    with pytest.raises(DomainError) as error:
        import_path(sources["gpkg"], tmp_path / "limited-vertices")
    assert error.value.kind == "source_limit"
    monkeypatch.setattr(vectors(), "MAX_FIELDS", 2)
    with pytest.raises(DomainError) as error:
        import_path(sources["gpkg"], tmp_path / "limited-fields")
    assert error.value.kind == "source_limit"


@pytest.mark.parametrize("geometry", [
    shapely.to_wkb(shapely.from_wkt("POINT M (1 2 3)")),
    struct.pack("<BII6d", 1, 8, 3, 0, 0, 1, 1, 2, 0),
])
def test_unknown_layer_type_does_not_hide_m_or_curves(tmp_path, geometry):
    path = tmp_path / "advanced.gpkg"
    table = pa.table({"name": ["advanced"], "geom": pa.array([geometry], type=pa.binary())})
    pyogrio.write_arrow(table, path, layer="land", driver="GPKG", geometry_name="geom", geometry_type="Unknown", crs="EPSG:4326")
    with pytest.raises(DomainError) as error:
        import_path(path, tmp_path / "advanced-work")
    assert error.value.kind == "unsupported_geometry"


def test_empty_layer_import_keeps_schema(tmp_path):
    path = tmp_path / "empty.gpkg"
    frame = gpd.GeoDataFrame({"code": pd.Series([], dtype="str")}, geometry=[], crs=4326)
    pyogrio.write_dataframe(frame, path, layer="land", geometry_type="Polygon", use_arrow=True)
    result = import_path(path, tmp_path / "empty-work")
    assert result["dataset"]["featureCount"] == 0
    assert result["dataset"]["bounds"] is None
    assert result["dataset"]["fields"][0]["name"] == "code"


def test_export_rejects_externally_modified_snapshot(sources, tmp_path):
    result = import_path(sources["gpkg"], tmp_path / "integrity")
    path = Path(result["artifactPath"])
    connection = sqlite3.connect(path)
    try:
        connection.execute("UPDATE gpkg_contents SET identifier = 'externally changed' WHERE table_name = 'features'")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(DomainError) as error:
        vectors().export_vector({"dataset": result["dataset"], "managedPath": str(path)}, tmp_path / "bad-export", lambda *args: None, lambda: False)
    assert error.value.kind == "snapshot_changed"
