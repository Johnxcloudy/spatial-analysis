from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

import pytest

from spatial_engine.errors import DomainError, InvalidParamsError
from spatial_engine.projects import ProjectStore
from spatial_engine.rpc import Engine
from spatial_engine.tasks import TaskManager
from spatial_engine.workspace import WorkspaceStore
from test_projects import valid_save
from test_workspace_tasks import _dataset, _table_dataset, _raster_dataset, _stage_import


def _setup(tmp_path):
    projects = ProjectStore()
    project = projects.create({"directory": str(tmp_path / "original"), "name": "Original"})
    workspace = WorkspaceStore(projects)
    workspace.get({"path": project["projectPath"]})
    return projects, project, workspace, TaskManager(projects, workspace)


def _publish(project, workspace, dataset):
    task_id = str(uuid.uuid4())
    _stage_import(project, workspace, dataset, task_id)
    workspace.complete_import_publication(task_id)
    return dataset


def _wait(manager, project, task):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        result = manager.get({"path": project["projectPath"], "taskId": task["id"]})
        if result["status"] != "running":
            return result
        time.sleep(0.05)
    raise AssertionError("worker timed out")


def test_mixed_save_as_preserves_original_metadata_and_snapshot_bytes(tmp_path):
    from test_cartography import valid_spec

    projects, project, workspace, manager = _setup(tmp_path)
    datasets = [_publish(project, workspace, factory()) for factory in (_dataset, _table_dataset, _raster_dataset)]
    parent = datasets[1]
    derived = _dataset()
    derived["source"].update(driver="TablePoints", path=str(workspace.managed_path(parent)),
                             fingerprint=parent["version"], metadata={"parentDatasetId": parent["id"], "parentVersion": parent["version"]})
    datasets.append(_publish(project, workspace, derived))
    layers = workspace.get({"path": project["projectPath"]})["layers"]
    vector_layer = next(layer for layer in layers if layer["datasetId"] == datasets[0]["id"])
    workspace.update_layer({"path": project["projectPath"], "layerId": vector_layer["id"], "changes": {
        "cartography": valid_spec(datasets[0]), "expectedCartographyRevision": 0,
    }})
    derived_layer = next(layer for layer in layers if layer["datasetId"] == derived["id"])
    workspace.update_layer({"path": project["projectPath"], "layerId": derived_layer["id"], "changes": {
        "cartography": None, "expectedCartographyRevision": 0,
    }})
    before = workspace.get({"path": project["projectPath"]})
    params = {**valid_save(project), "directory": str(tmp_path / "copy")}
    task = manager.start_save_as(params)
    completed = _wait(manager, project, task)
    assert completed["status"] == "completed", completed
    assert completed["destination"] == str(tmp_path / "copy" / "project.spa")
    assert projects.open({"path": project["projectPath"]}) == project
    original_path = Path(project["projectPath"])
    original_hashes = {d["relativePath"]: hashlib.sha256(workspace.managed_path(d).read_bytes()).hexdigest() for d in datasets}
    manager.close()
    projects.close()
    shutil.move(tmp_path / "copy", tmp_path / "moved-copy")
    copied = projects.open({"path": str(tmp_path / "moved-copy" / "project.spa")})
    assert copied["id"] != project["id"]
    assert copied["name"] == params["name"]
    assert copied["viewState"] == params["viewState"]
    assert copied["description"] == params["description"]
    after = workspace.get({"path": copied["projectPath"]})
    assert after["datasets"] == before["datasets"]
    assert after["layers"] == before["layers"]
    assert after["tasks"] == before["tasks"]
    assert {d["relativePath"]: hashlib.sha256(workspace.managed_path(d).read_bytes()).hexdigest() for d in datasets} == original_hashes
    status = workspace.source_status({"path": copied["projectPath"], "datasetId": derived["id"]})
    assert status["availability"] == "internal"
    assert status["resolvedPath"] == str(Path(copied["projectPath"]).parent / parent["relativePath"])
    assert original_path.is_file()
    projects.close()


@pytest.mark.parametrize("issue", ["missing", "changed"])
def test_save_as_rejects_missing_or_changed_snapshot(tmp_path, issue):
    projects, project, workspace, manager = _setup(tmp_path)
    dataset = _publish(project, workspace, _dataset())
    managed = workspace.managed_path(dataset)
    if issue == "missing":
        managed.unlink()
    else:
        managed.write_bytes(b"changed snapshot")
    try:
        task = manager.start_save_as({**valid_save(project), "directory": str(tmp_path / "copy")})
        result = _wait(manager, project, task)
        assert result["status"] == "failed", result
    except DomainError as exc:
        assert exc.kind in {"dataset_missing", "snapshot_changed"}
    assert not (tmp_path / "copy").exists()
    manager.close()
    projects.close()


@pytest.mark.parametrize("target", ["original", "original/child", "existing"])
def test_save_as_refuses_existing_or_nested_target(tmp_path, target):
    projects, project, workspace, manager = _setup(tmp_path)
    (tmp_path / "existing").mkdir()
    with pytest.raises(DomainError):
        manager.start_save_as({**valid_save(project), "directory": str(tmp_path / target)})
    assert workspace.get({"path": project["projectPath"]})["tasks"] == []
    projects.close()


def test_relocation_accepts_renamed_csv_without_mutating_provenance(tmp_path):
    from spatial_engine.vectors import source_fingerprint

    projects, project, workspace, manager = _setup(tmp_path)
    original = tmp_path / "original.csv"
    original.write_text("code,x,y\n001,114,27\n", encoding="utf-8")
    dataset = _table_dataset()
    dataset["source"].update(path=str(original), fingerprint=source_fingerprint(original))
    dataset = _publish(project, workspace, dataset)
    renamed = original.rename(tmp_path / "renamed.csv")
    assert workspace.source_status({"path": project["projectPath"], "datasetId": dataset["id"]})["availability"] == "missing"
    task = manager.start_relocation({"path": project["projectPath"], "datasetId": dataset["id"], "sourcePath": str(renamed)})
    assert _wait(manager, project, task)["status"] == "completed"
    status = workspace.source_status({"path": project["projectPath"], "datasetId": dataset["id"]})
    assert status == {"datasetId": dataset["id"], "originalPath": str(original), "resolvedPath": str(renamed),
                      "availability": "present", "relocated": True, "verifiedAt": status["verifiedAt"]}
    assert status["verifiedAt"] is not None
    assert workspace.dataset(project["projectPath"], dataset["id"])["source"] == dataset["source"]
    renamed.write_text("changed", encoding="utf-8")
    assert workspace.source_status({"path": project["projectPath"], "datasetId": dataset["id"]}) == status
    task = manager.start_relocation({"path": project["projectPath"], "datasetId": dataset["id"], "sourcePath": str(renamed)})
    assert _wait(manager, project, task)["status"] == "failed"
    assert workspace.source_status({"path": project["projectPath"], "datasetId": dataset["id"]}) == status
    manager.close()
    projects.close()


def _prepared_copy(tmp_path, *, prepare_publication=True):
    from spatial_engine import portability
    projects, project, workspace, manager = _setup(tmp_path)
    manager.close()
    del manager
    _publish(project, workspace, _dataset())
    task_id = str(uuid.uuid4())
    options = portability.validate_save_as({**valid_save(project), "directory": str(tmp_path / "copy")}, Path(project["projectPath"]))
    payload = portability.prepare_copy(workspace, task_id, str(uuid.uuid4()), options)
    work_dir = Path(project["projectPath"]).parent / "staging" / "tasks" / task_id
    work_dir.mkdir(parents=True)
    result = portability.copy_project(payload, work_dir, progress=lambda *args: None, cancelled=lambda: False)
    if prepare_publication:
        portability.prepare_copy_publication(workspace, task_id, result)
    return projects, project, workspace, task_id, payload, result, work_dir


@pytest.mark.parametrize("point", ["before_rename", "after_rename", "after_marker_removal", "before_journal"])
def test_copy_publication_recovers_each_crash_boundary(tmp_path, monkeypatch, point):
    from spatial_engine import portability
    projects, project, workspace, task_id, payload, result, work_dir = _prepared_copy(tmp_path, prepare_publication=point != "before_journal")
    temporary = Path(payload["temporaryPath"])
    destination = Path(payload["options"]["directory"])
    if point == "before_journal":
        (work_dir / "result.json").write_text(json.dumps({"ok": True, "result": result}), encoding="utf-8")
    elif point == "before_rename":
        real_rename = portability._rename_directory_no_replace
        monkeypatch.setattr(portability, "_rename_directory_no_replace", lambda *args: (_ for _ in ()).throw(PermissionError("injected")))
        with pytest.raises(DomainError, match="Could not publish"):
            portability.complete_copy_publication(workspace, task_id)
        monkeypatch.setattr(portability, "_rename_directory_no_replace", real_rename)
    else:
        portability._rename_directory_no_replace(temporary, destination)
        if point == "after_marker_removal":
            (destination / portability.COPY_MARKER).unlink()
    pending_root = destination if destination.exists() else temporary
    if point != "after_marker_removal":
        contender = ProjectStore()
        with pytest.raises(DomainError) as exc:
            contender.open({"path": str(pending_root / "project.spa")})
        assert exc.value.kind == "copy_pending"
        contender.close()
    projects.close()
    projects.open({"path": project["projectPath"]})
    workspace.get({"path": project["projectPath"]})
    assert workspace.task(task_id)["status"] == "completed"
    assert not workspace.has_pending(task_id)
    assert not (destination / portability.COPY_MARKER).exists()
    projects.close()


def test_save_as_rejects_linked_snapshot_directory_without_touching_target(tmp_path):
    import os
    import subprocess
    projects, project, workspace, manager = _setup(tmp_path)
    dataset = _publish(project, workspace, _dataset())
    directory = Path(project["projectPath"]).parent / "datasets"
    external = tmp_path / "external-snapshots"
    directory.rename(external)
    if os.name == "nt":
        created = subprocess.run(["cmd", "/c", "mklink", "/J", str(directory), str(external)], capture_output=True)
        assert created.returncode == 0, created.stderr
    else:
        directory.symlink_to(external, target_is_directory=True)
    try:
        with pytest.raises(DomainError) as exc:
            manager.start_save_as({**valid_save(project), "directory": str(tmp_path / "copy")})
        assert exc.value.kind == "unsafe_path"
        assert (external / Path(dataset["relativePath"]).name).read_bytes() == b"validated snapshot"
        assert not (tmp_path / "copy").exists()
    finally:
        if os.name == "nt":
            directory.rmdir()
        else:
            directory.unlink()
        projects.close()


def test_copy_rejects_registry_path_escape_and_invalid_source_history(tmp_path):
    projects, project, workspace, manager = _setup(tmp_path)
    dataset = _publish(project, workspace, _dataset())
    with sqlite3.connect(project["projectPath"]) as connection:
        connection.execute("DROP TRIGGER datasets_no_update")
        changed = {**dataset, "relativePath": "../escape.gpkg"}
        connection.execute("UPDATE datasets SET dataset_json = ?", (json.dumps(changed),))
    with pytest.raises(DomainError) as exc:
        manager.start_save_as({**valid_save(project), "directory": str(tmp_path / "copy")})
    assert exc.value.kind == "invalid_project"
    assert not (tmp_path / "copy").exists()
    projects.close()


def test_copy_excludes_unregistered_files_preserves_terminal_tasks_and_relocation_history(tmp_path):
    from spatial_engine import portability
    from spatial_engine.vectors import source_fingerprint
    projects, project, workspace, manager = _setup(tmp_path)
    source = tmp_path / "source.csv"
    source.write_bytes(b"code\n1\n")
    dataset = _table_dataset()
    dataset["source"].update(path=str(source), driver="CSV", fingerprint=source_fingerprint(source))
    _publish(project, workspace, dataset)
    relocated = source.rename(tmp_path / "relocated.csv")
    task = manager.start_relocation({"path": project["projectPath"], "datasetId": dataset["id"], "sourcePath": str(relocated)})
    assert _wait(manager, project, task)["status"] == "completed"
    for status in ("failed", "cancelled", "interrupted"):
        historical = workspace.create_task("export", destination=str(tmp_path / "provenance.gpkg"))
        workspace.update_task(historical["id"], status=status, stage=status, error="Historical test state")
    original_tasks = workspace.get({"path": project["projectPath"]})["tasks"]
    root = Path(project["projectPath"]).parent
    for relative in ("cache/extra.bin", "results/extra.bin", "backups/extra.spa", "datasets/unregistered.gpkg"):
        (root / relative).write_bytes(b"not authoritative")
    task = manager.start_save_as({**valid_save(project), "directory": str(tmp_path / "copy")})
    assert _wait(manager, project, task)["status"] == "completed"
    manager.close()
    projects.close()
    copied = projects.open({"path": str(tmp_path / "copy" / "project.spa")})
    assert workspace.get({"path": copied["projectPath"]})["tasks"] == original_tasks
    assert workspace.source_status({"path": copied["projectPath"], "datasetId": dataset["id"]})["resolvedPath"] == str(relocated)
    assert not list((tmp_path / "copy").rglob("extra.*"))
    assert not (tmp_path / "copy" / "datasets" / "unregistered.gpkg").exists()
    with sqlite3.connect(copied["projectPath"]) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE source_locations SET source_path = 'changed'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM source_locations")
    projects.close()


def test_actual_task_cancellation_and_mutation_barrier(tmp_path):
    import sys
    engine = Engine()
    project = engine.dispatch("project.create", {"directory": str(tmp_path / "original"), "name": "Original"})
    engine.tasks._command_factory = lambda request: [sys.executable, "-c", "import time; time.sleep(60)"]
    task = engine.dispatch("project.saveAs", {**valid_save(project), "directory": str(tmp_path / "copy")})
    for method, params in [("project.save", valid_save(project)),
                           ("layer.remove", {"path": project["projectPath"], "layerId": "unused"})]:
        with pytest.raises(DomainError) as exc:
            engine.dispatch(method, params)
        assert exc.value.kind == "task_busy"
    cancelled = engine.dispatch("task.cancel", {"path": project["projectPath"], "taskId": task["id"]})
    assert cancelled["status"] == "cancelled"
    assert not engine.workspace.has_pending(task["id"])
    assert not (tmp_path / "copy").exists()
    assert engine.projects.open({"path": project["projectPath"]}) == project
    engine.close()


def test_copy_target_collision_preserves_unrelated_directory_and_pending_copy(tmp_path):
    from spatial_engine import portability
    projects, project, workspace, task_id, payload, result, _ = _prepared_copy(tmp_path)
    destination = Path(payload["options"]["directory"])
    destination.mkdir()
    unrelated = destination / "user.txt"
    unrelated.write_text("keep", encoding="utf-8")
    projects.close()
    projects.open({"path": project["projectPath"]})
    workspace.get({"path": project["projectPath"]})
    assert workspace.task(task_id)["status"] == "interrupted"
    assert workspace.has_pending(task_id)
    assert unrelated.read_text() == "keep"
    assert Path(payload["temporaryPath"]).is_dir()
    with pytest.raises(DomainError) as exc:
        TaskManager(projects, workspace).start_save_as({**valid_save(project), "directory": str(tmp_path / "another")})
    assert exc.value.kind == "publication_pending"
    projects.close()


def test_cancel_during_copy_removes_only_owned_pending_files(tmp_path):
    from spatial_engine import portability
    projects, project, workspace, manager = _setup(tmp_path)
    dataset = _publish(project, workspace, _dataset())
    managed = workspace.managed_path(dataset)
    original_bytes = managed.read_bytes()
    task_id = str(uuid.uuid4())
    options = portability.validate_save_as({**valid_save(project), "directory": str(tmp_path / "copy")}, Path(project["projectPath"]))
    payload = portability.prepare_copy(workspace, task_id, str(uuid.uuid4()), options)
    task_dir = Path(project["projectPath"]).parent / "staging" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    stop = False
    def progress(stage, *args):
        nonlocal stop
        if stage == "copying":
            stop = True
    with pytest.raises(DomainError) as exc:
        portability.copy_project(payload, task_dir, progress=progress, cancelled=lambda: stop)
    assert exc.value.kind == "task_cancelled"
    result = manager.cancel({"path": project["projectPath"], "taskId": task_id})
    assert result["status"] == "cancelled"
    assert not Path(payload["temporaryPath"]).exists()
    assert not (tmp_path / "copy").exists()
    assert managed.read_bytes() == original_bytes
    assert projects.open({"path": project["projectPath"]}) == project
    projects.close()


def test_cancel_retains_foreign_files_and_never_publishes_cancelled_copy(tmp_path):
    from spatial_engine import portability
    projects, project, workspace, task_id, payload, result, _ = _prepared_copy(tmp_path)
    temporary = Path(payload["temporaryPath"])
    unrelated = temporary / "user.txt"
    unrelated.write_text("keep", encoding="utf-8")
    cancelled = TaskManager(projects, workspace).cancel({"path": project["projectPath"], "taskId": task_id})
    assert cancelled["status"] == "cancelled"
    assert workspace.has_pending(task_id)
    projects.close()
    projects.open({"path": project["projectPath"]})
    workspace.get({"path": project["projectPath"]})
    assert workspace.task(task_id)["status"] == "cancelled"
    assert unrelated.read_text() == "keep"
    assert not Path(payload["options"]["directory"]).exists()
    projects.close()


@pytest.mark.parametrize("driver", ["ESRI Shapefile", "OpenFileGDB", "GTiff", "GPKG", "XLSX"])
def test_relocation_fingerprint_handles_renamed_sources_and_bundles(tmp_path, driver):
    from spatial_engine import portability
    from spatial_engine.vectors import source_fingerprint
    if driver == "OpenFileGDB":
        original = tmp_path / "old.gdb"
        original.mkdir()
        (original / "a00000001.gdbtable").write_bytes(b"bundle table")
        (original / "nested").mkdir()
        (original / "nested" / "a.dat").write_bytes(b"nested component")
    else:
        suffix = {"ESRI Shapefile": ".shp", "GTiff": ".tif", "GPKG": ".gpkg", "XLSX": ".xlsx"}[driver]
        original = tmp_path / ("old" + suffix)
        original.write_bytes(b"original raw bytes")
        if driver == "ESRI Shapefile":
            for suffix in (".shx", ".dbf", ".prj", ".cpg"):
                original.with_suffix(suffix).write_bytes(suffix.encode())
    dataset = _dataset()
    fingerprint = hashlib.sha256(original.read_bytes()).hexdigest() if driver == "GTiff" else source_fingerprint(original)
    dataset["source"].update(path=str(original), driver=driver, fingerprint=fingerprint)
    if driver == "ESRI Shapefile":
        for component in list(tmp_path.glob("old.*")):
            component.rename(component.with_name("renamed" + component.suffix))
        candidate = tmp_path / "renamed.shp"
    else:
        candidate = original.rename(original.with_name("renamed" + original.suffix))
    result = portability.relocate_source({"dataset": dataset, "sourcePath": str(candidate)}, tmp_path,
                                         progress=lambda *args: None, cancelled=lambda: False)
    assert result["fingerprint"] == fingerprint
    assert result["sourcePath"] == str(candidate)


def test_relocation_cancellation_and_internal_source_validation(tmp_path):
    from spatial_engine import portability
    projects, project, workspace, manager = _setup(tmp_path)
    dataset = _dataset()
    dataset["source"].update(driver="TablePoints", metadata={"parentDatasetId": str(uuid.uuid4()), "parentVersion": "0" * 64})
    _publish(project, workspace, dataset)
    with pytest.raises(DomainError) as exc:
        manager.start_relocation({"path": project["projectPath"], "datasetId": dataset["id"], "sourcePath": str(tmp_path / "unused.gpkg")})
    assert exc.value.kind == "internal_source"
    assert workspace.source_status({"path": project["projectPath"], "datasetId": dataset["id"]})["availability"] == "unavailable"
    with pytest.raises(DomainError) as exc:
        manager.start_save_as({**valid_save(project), "directory": str(tmp_path / "copy")})
    assert exc.value.kind == "invalid_project"
    source = tmp_path / "candidate.gpkg"
    source.write_bytes(b"abc")
    external = _dataset()
    external["source"].update(driver="GPKG", path=str(source))
    with pytest.raises(DomainError) as exc:
        portability.relocate_source({"dataset": external, "sourcePath": str(source)}, tmp_path,
                                    progress=lambda *args: None, cancelled=lambda: True)
    assert exc.value.kind == "task_cancelled"
    projects.close()


def test_save_as_budget_and_protocol_validation(tmp_path, monkeypatch):
    from spatial_engine import portability
    projects, project, workspace, manager = _setup(tmp_path)
    monkeypatch.setattr(portability, "MAX_COPY_BYTES", 1)
    with pytest.raises(DomainError) as exc:
        manager.start_save_as({**valid_save(project), "directory": str(tmp_path / "copy")})
    assert exc.value.kind == "copy_limit"
    with pytest.raises(InvalidParamsError):
        manager.start_save_as({**valid_save(project), "directory": str(tmp_path / "copy"), "extra": True})
    with pytest.raises(InvalidParamsError):
        workspace.source_status({"path": project["projectPath"], "datasetId": "unused", "extra": True})
    assert workspace.get({"path": project["projectPath"]})["tasks"] == []
    projects.close()


def test_schema_four_migration_preserves_registry_json_history_and_bytes(tmp_path):
    import spatial_engine.projects as project_module
    projects, project, workspace, manager = _setup(tmp_path)
    _publish(project, workspace, _raster_dataset())
    original_workspace = workspace.get({"path": project["projectPath"]})
    projects.close()
    path = Path(project["projectPath"])
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE source_locations")
        connection.execute("DROP TABLE pending_copies")
        task_rows = connection.execute("SELECT * FROM tasks").fetchall()
        connection.execute("DROP TABLE tasks")
        project_module._create_v4_schema(connection)
        connection.executemany("INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", task_rows)
        connection.execute("PRAGMA user_version = 4")
        raw_json = connection.execute("SELECT dataset_json FROM datasets").fetchall()
    original_bytes = {d["relativePath"]: (path.parent / d["relativePath"]).read_bytes() for d in original_workspace["datasets"]}
    reopened = projects.open({"path": str(path)})
    assert reopened["schemaVersion"] == 7
    assert workspace.get({"path": str(path)}) == original_workspace
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT dataset_json FROM datasets").fetchall() == raw_json
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    backup = list((path.parent / "backups").glob("project-v4-*.spa"))
    assert len(backup) == 1
    with sqlite3.connect(backup[0]) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (4,)
        assert connection.execute("SELECT dataset_json FROM datasets").fetchall() == raw_json
    assert {relative: (path.parent / relative).read_bytes() for relative in original_bytes} == original_bytes
    projects.close()


def test_copy_journal_creation_failure_rolls_back_task_and_allows_retry(tmp_path, monkeypatch):
    from contextlib import contextmanager
    projects, project, workspace, manager = _setup(tmp_path)
    real_connect = workspace._connect

    class RejectJournal:
        def __init__(self, connection):
            self.connection = connection
        def execute(self, sql, *args):
            if sql.startswith("INSERT INTO pending_copies"):
                raise sqlite3.OperationalError("injected journal failure")
            return self.connection.execute(sql, *args)

    @contextmanager
    def failing_connect(path):
        with real_connect(path) as connection:
            yield RejectJournal(connection)

    monkeypatch.setattr(workspace, "_connect", failing_connect)
    params = {**valid_save(project), "directory": str(tmp_path / "copy")}
    with pytest.raises(sqlite3.OperationalError, match="injected journal failure"):
        manager.start_save_as(params)
    monkeypatch.setattr(workspace, "_connect", real_connect)
    assert workspace.running_task() is None
    assert workspace.get({"path": project["projectPath"]})["tasks"] == []
    task = manager.start_save_as(params)
    assert _wait(manager, project, task)["status"] == "completed"
    manager.close()
    projects.close()


@pytest.mark.parametrize("column,value", [("source_path", "x" * 32768), ("verified_at", "invalid"), ("fingerprint", "different")],
                         ids=["oversized_path", "invalid_timestamp", "mismatched_fingerprint"])
def test_source_status_rejects_corrupt_history(tmp_path, column, value):
    from spatial_engine.vectors import source_fingerprint
    projects, project, workspace, manager = _setup(tmp_path)
    source = tmp_path / "source.csv"
    source.write_bytes(b"code\n1\n")
    dataset = _table_dataset()
    dataset["source"].update(path=str(source), driver="CSV", fingerprint=source_fingerprint(source))
    _publish(project, workspace, dataset)
    task = manager.start_relocation({"path": project["projectPath"], "datasetId": dataset["id"], "sourcePath": str(source)})
    assert _wait(manager, project, task)["status"] == "completed"
    with sqlite3.connect(project["projectPath"]) as connection:
        connection.execute("DROP TRIGGER source_locations_no_update")
        connection.execute(f"UPDATE source_locations SET {column} = ?", (value,))
    with pytest.raises(DomainError) as exc:
        workspace.source_status({"path": project["projectPath"], "datasetId": dataset["id"]})
    assert exc.value.kind == "invalid_project"
    manager.close()
    projects.close()


@pytest.mark.parametrize("move_directory", [False, True])
def test_relocation_accepts_unchanged_and_moved_mixed_case_shapefile_components(tmp_path, move_directory):
    from spatial_engine import portability
    from spatial_engine.vectors import source_fingerprint
    original_dir = tmp_path / "original-bundle"
    original_dir.mkdir()
    original = original_dir / "Roads.shp"
    for name in ("Roads.shp", "roads.shx", "roads.dbf", "ROADS.prj"):
        (original_dir / name).write_bytes(name.encode("ascii"))
    dataset = _dataset()
    dataset["source"].update(path=str(original), driver="ESRI Shapefile", fingerprint=source_fingerprint(original))
    candidate = original
    if move_directory:
        moved = original_dir.rename(tmp_path / "moved-bundle")
        candidate = moved / original.name
    result = portability.relocate_source({"dataset": dataset, "sourcePath": str(candidate)}, tmp_path,
                                         progress=lambda *args: None, cancelled=lambda: False)
    assert result["fingerprint"] == dataset["source"]["fingerprint"]
    candidate.with_name("roads.dbf").write_bytes(b"changed attributes")
    with pytest.raises(DomainError) as exc:
        portability.relocate_source({"dataset": dataset, "sourcePath": str(candidate)}, tmp_path,
                                    progress=lambda *args: None, cancelled=lambda: False)
    assert exc.value.kind == "source_mismatch"
