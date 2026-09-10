from __future__ import annotations

import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from spatial_engine.errors import DomainError, InvalidParamsError
import spatial_engine.projects as project_module
from test_workspace_tasks import _dataset, _raster_dataset, _stage_import, _stores


def valid_spec(dataset: dict, revision: int = 1) -> dict:
    return {
        "specVersion": 1, "kind": "vector-layer", "revision": revision,
        "input": {"datasetId": dataset["id"], "version": dataset["version"]},
        "basePreset": "planning", "presetVersion": 1,
        "symbol": {"fillColor": "#123abc", "strokeColor": "#112233", "strokeWidthPt": 1.2, "pointRadiusPt": 4},
        "renderer": {
            "kind": "categorized", "field": "code",
            "categories": [{"value": value, "label": f"Entry {index}", "color": "#ABCDEF"}
                           for index, value in enumerate(["null", "", "01", 0, False, "1", 1, "9007199254740992"])],
            "nullColor": "#000000", "nullLabel": "NULL", "otherColor": "#ffffff", "otherLabel": "Other",
        },
        "legend": {"visible": True, "title": "Configured symbols"},
    }


@pytest.fixture
def stored_layer(tmp_path):
    projects, project, workspace = _stores(tmp_path)
    dataset = _dataset()
    task_id = str(uuid.uuid4())
    _stage_import(project, workspace, dataset, task_id)
    workspace.complete_import_publication(task_id)
    layer = workspace.get({"path": project["projectPath"]})["layers"][0]
    yield projects, project, workspace, dataset, layer
    projects.close()


def adopt(workspace, project, layer, spec, expected=0, **other):
    return workspace.update_layer({"path": project["projectPath"], "layerId": layer["id"],
                                   "changes": {"cartography": spec, "expectedCartographyRevision": expected, **other}})


def test_adoption_roundtrip_preserves_legacy_dataset_and_snapshot(stored_layer):
    projects, project, workspace, dataset, layer = stored_layer
    layer = workspace.update_layer({"path": project["projectPath"], "layerId": layer["id"],
                                   "changes": {"color": "#445566", "categoryField": "code", "categoryColors": {"null": "#123456"}}})
    snapshot_bytes = workspace.managed_path(dataset).read_bytes()
    before = workspace.get({"path": project["projectPath"]})
    spec = valid_spec(dataset)
    updated = adopt(workspace, project, layer, spec)
    assert updated["cartography"] == spec
    assert updated["cartographyRevision"] == 1
    assert {key: updated[key] for key in layer} == layer
    projects.close()
    projects.open({"path": project["projectPath"]})
    reopened = workspace.get({"path": project["projectPath"]})
    assert reopened["layers"][0] == updated
    assert reopened["datasets"] == before["datasets"]
    assert reopened["tasks"] == before["tasks"]
    assert workspace.managed_path(dataset).read_bytes() == snapshot_bytes


def test_restore_retains_revision_and_prevents_stale_reapplication(stored_layer):
    _, project, workspace, dataset, layer = stored_layer
    adopt(workspace, project, layer, valid_spec(dataset))
    restored = adopt(workspace, project, layer, None, expected=1)
    assert restored["cartography"] is None
    assert restored["cartographyRevision"] == 2
    assert {key: restored[key] for key in layer} == layer
    with pytest.raises(DomainError) as error:
        adopt(workspace, project, layer, valid_spec(dataset, 2), expected=1, name="Stale")
    assert error.value.kind == "cartography_conflict"
    assert workspace.get({"path": project["projectPath"]})["layers"][0] == restored
    assert adopt(workspace, project, layer, valid_spec(dataset, 3), expected=2)["cartographyRevision"] == 3


def test_active_spec_rejects_legacy_changes_and_combined_edits(stored_layer):
    _, project, workspace, dataset, layer = stored_layer
    adopt(workspace, project, layer, valid_spec(dataset))
    for changes in ({"color": "#ffffff"}, {"categoryField": None}, {"categoryColors": {}},
                    {"cartography": None, "expectedCartographyRevision": 1, "color": "#ffffff"}):
        with pytest.raises(InvalidParamsError) as error:
            workspace.update_layer({"path": project["projectPath"], "layerId": layer["id"], "changes": changes})
        assert "cartography" in error.value.detail
    updated = workspace.update_layer({"path": project["projectPath"], "layerId": layer["id"],
                                      "changes": {"name": "Visible style", "opacity": 0.4, "visible": False}})
    assert updated["cartographyRevision"] == 1
    assert updated["name"] == "Visible style"
    assert updated["opacity"] == 0.4 and updated["visible"] is False


@pytest.mark.parametrize("path,value", [
    (("specVersion",), 2), (("specVersion",), True), (("kind",), "page"), (("revision",), 2),
    (("revision",), True), (("input", "datasetId"), str(uuid.uuid4())), (("input", "version"), "f" * 64),
    (("input", "path"), "C:/arbitrary.gpkg"), (("basePreset",), "nature"), (("basePreset",), []),
    (("presetVersion",), 2), (("presetVersion",), True), (("symbol", "fillColor"), "red"),
    (("symbol", "strokeColor"), "#ffffffff"), (("symbol", "strokeWidthPt"), -1),
    (("symbol", "strokeWidthPt"), 6.01), (("symbol", "strokeWidthPt"), float("inf")),
    (("symbol", "pointRadiusPt"), 0), (("symbol", "pointRadiusPt"), 16.1),
    (("symbol", "pointRadiusPt"), True), (("renderer", "field"), "missing"),
    (("renderer", "kind"), "graduated"), (("renderer", "nullColor"), "transparent"),
    (("renderer", "nullLabel"), "x" * 121), (("legend", "title"), "x" * 121),
    (("legend", "visible"), 1), (("script",), "run()"),
    (("renderer", "categories"), [{"value": None, "label": "Null", "color": "#ffffff"}]),
    (("renderer", "categories"), [{"value": 9007199254740992, "label": "Unsafe", "color": "#ffffff"}]),
    (("renderer", "categories"), [{"value": float("nan"), "label": "NaN", "color": "#ffffff"}]),
    (("renderer", "categories"), [{"value": "x" * 1025, "label": "Long", "color": "#ffffff"}]),
    (("renderer", "categories"), [{"value": 1, "label": "x" * 121, "color": "#ffffff"}]),
    (("renderer", "categories"), [{"value": value, "label": "Duplicate", "color": "#ffffff"} for value in [1, 1.0]]),
    (("renderer", "categories"), [{"value": value, "label": "Category", "color": "#ffffff"} for value in range(65)]),
    (("renderer", "categories"), [{"value": str(value) + "x" * 1000, "label": "Category", "color": "#ffffff"} for value in range(40)]),
])
def test_invalid_spec_rejected_atomically(stored_layer, path, value):
    _, project, workspace, dataset, layer = stored_layer
    spec = valid_spec(dataset)
    target = spec
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(InvalidParamsError) as error:
        adopt(workspace, project, layer, spec, name="Must roll back")
    assert "cartography" in error.value.detail
    assert workspace.get({"path": project["projectPath"]})["layers"][0] == layer


@pytest.mark.parametrize("changes", [{"cartography": None}, {"expectedCartographyRevision": 0},
                                    {"cartography": None, "expectedCartographyRevision": True},
                                    {"cartography": None, "expectedCartographyRevision": -1}])
def test_revision_is_required_only_with_cartography(stored_layer, changes):
    _, project, workspace, _, layer = stored_layer
    with pytest.raises(InvalidParamsError) as error:
        workspace.update_layer({"path": project["projectPath"], "layerId": layer["id"], "changes": changes})
    assert "cartography" in error.value.detail.lower()


def test_single_symbol_limits_and_literal_text_are_preserved(stored_layer):
    _, project, workspace, dataset, layer = stored_layer
    spec = valid_spec(dataset)
    spec["basePreset"] = "publication"
    spec["renderer"] = {"kind": "single"}
    spec["symbol"].update(strokeWidthPt=0, pointRadiusPt=16)
    spec["legend"] = {"visible": False, "title": "  中文 title  "}
    assert adopt(workspace, project, layer, spec)["cartography"] == spec


@pytest.mark.parametrize("corruption", ["json", "version", "revision", "field", "oversize", "kind", "nonfinite", "surrogate"])
def test_stored_corrupt_spec_rejected_on_read_and_update(stored_layer, corruption):
    _, project, workspace, dataset, layer = stored_layer
    spec = valid_spec(dataset)
    adopt(workspace, project, layer, spec)
    if corruption == "version":
        spec["specVersion"] = 999
    elif corruption == "revision":
        spec["revision"] = 8
    elif corruption == "field":
        spec["renderer"]["field"] = "missing"
    elif corruption == "kind":
        spec["kind"] = "raster"
    elif corruption == "nonfinite":
        spec["symbol"]["strokeWidthPt"] = float("inf")
    elif corruption == "surrogate":
        spec["legend"]["title"] = "\ud800"
    raw = "{broken" if corruption == "json" else " " * 32769 if corruption == "oversize" else json.dumps(spec)
    with sqlite3.connect(project["projectPath"]) as connection:
        connection.execute("UPDATE vector_cartography SET spec_json = ?", (raw,))
    for operation in (lambda: workspace.get({"path": project["projectPath"]}),
                      lambda: workspace.update_layer({"path": project["projectPath"], "layerId": layer["id"], "changes": {"name": "Changed"}})):
        with pytest.raises(DomainError) as error:
            operation()
        assert error.value.kind == "invalid_project"
    with sqlite3.connect(project["projectPath"]) as connection:
        assert connection.execute("SELECT name FROM map_layers WHERE layer_id = ?", (layer["id"],)).fetchone()[0] == layer["name"]


def test_raster_rejects_cartography_and_layer_removal_cascades(stored_layer):
    _, project, workspace, dataset, layer = stored_layer
    raster = _raster_dataset()
    task_id = str(uuid.uuid4())
    _stage_import(project, workspace, raster, task_id)
    workspace.complete_import_publication(task_id)
    raster_layer = next(item for item in workspace.get({"path": project["projectPath"]})["layers"] if item["datasetId"] == raster["id"])
    with pytest.raises(InvalidParamsError):
        adopt(workspace, project, raster_layer, None)
    adopt(workspace, project, layer, valid_spec(dataset))
    reordered = workspace.reorder_layers({"path": project["projectPath"], "layerIds": [raster_layer["id"], layer["id"]]})
    assert reordered[1]["cartographyRevision"] == 1
    workspace.remove_layer({"path": project["projectPath"], "layerId": layer["id"]})
    with sqlite3.connect(project["projectPath"]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM vector_cartography").fetchone()[0] == 0
    assert workspace.dataset(project["projectPath"], dataset["id"]) == dataset


def test_schema_six_migration_preserves_legacy_and_creates_backup(stored_layer):
    projects, project, workspace, dataset, layer = stored_layer
    before = workspace.get({"path": project["projectPath"]})
    projects.close()
    with sqlite3.connect(project["projectPath"]) as connection:
        connection.execute("DROP TABLE IF EXISTS vector_cartography")
        connection.execute("PRAGMA user_version = 6")
    reopened = projects.open({"path": project["projectPath"]})
    assert reopened["schemaVersion"] == 7
    assert workspace.get({"path": project["projectPath"]}) == before
    backups = list((Path(project["projectPath"]).parent / "backups").glob("project-v6-*.spa"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        assert connection.execute("SELECT color, category_colors FROM map_layers").fetchone() == (layer["color"], "{}")
    assert adopt(workspace, project, layer, valid_spec(dataset))["cartographyRevision"] == 1


def test_concurrent_drafts_have_only_one_winner(stored_layer):
    _, project, workspace, dataset, layer = stored_layer

    def submit(name):
        try:
            return adopt(workspace, project, layer, valid_spec(dataset), name=name)
        except DomainError as error:
            return error.kind

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ["First draft", "Second draft"]))
    assert results.count("cartography_conflict") == 1
    winner = next(item for item in results if isinstance(item, dict))
    assert winner["cartographyRevision"] == 1
    assert workspace.get({"path": project["projectPath"]})["layers"][0] == winner


def test_schema_six_migration_failure_rolls_back_table_and_keeps_backup(stored_layer, monkeypatch):
    projects, project, _, _, _ = stored_layer
    projects.close()
    with sqlite3.connect(project["projectPath"]) as connection:
        connection.execute("DROP TABLE vector_cartography")
        connection.execute("PRAGMA user_version = 6")
    real_create = project_module._create_v7_schema

    def fail_after_create(connection):
        real_create(connection)
        raise sqlite3.OperationalError("injected failure after adding table")

    monkeypatch.setattr(project_module, "_create_v7_schema", fail_after_create)
    with pytest.raises(DomainError) as error:
        projects.open({"path": project["projectPath"]})
    assert error.value.kind == "project_migration_failed"
    with sqlite3.connect(project["projectPath"]) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'vector_cartography'").fetchone() is None
    assert len(list((Path(project["projectPath"]).parent / "backups").glob("project-v6-*.spa"))) == 1
    monkeypatch.setattr(project_module, "_create_v7_schema", real_create)
    assert projects.open({"path": project["projectPath"]})["schemaVersion"] == 7


@pytest.mark.parametrize("layer_column,revision_column,spec_column", [
    ("layer_id TEXT NOT NULL", "revision INTEGER NOT NULL", "spec_json TEXT"),
    ("layer_id TEXT PRIMARY KEY NOT NULL", "revision TEXT NOT NULL", "spec_json TEXT"),
    ("layer_id TEXT PRIMARY KEY NOT NULL", "revision INTEGER", "spec_json TEXT"),
    ("layer_id TEXT PRIMARY KEY", "revision INTEGER NOT NULL", "spec_json TEXT"),
    ("layer_id TEXT PRIMARY KEY NOT NULL", "revision INTEGER NOT NULL", "spec_json BLOB"),
])
def test_project_rejects_malformed_cartography_schema(stored_layer, layer_column, revision_column, spec_column):
    projects, project, _, _, _ = stored_layer
    projects.close()
    with sqlite3.connect(project["projectPath"]) as connection:
        connection.execute("DROP TABLE vector_cartography")
        connection.execute(f"CREATE TABLE vector_cartography ({layer_column} REFERENCES map_layers(layer_id) ON DELETE CASCADE, {revision_column}, {spec_column})")
    with pytest.raises(DomainError) as error:
        projects.open({"path": project["projectPath"]})
    assert error.value.kind == "invalid_project"
