from __future__ import annotations

import json
import hashlib
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from spatial_engine.errors import DomainError, InvalidParamsError
from spatial_engine.projects import ProjectStore
from spatial_engine.tasks import TaskManager
from spatial_engine.workspace import WorkspaceStore


def _stores(tmp_path: Path):
    projects = ProjectStore()
    project = projects.create({"directory": str(tmp_path / "project"), "name": "Workspace"})
    workspace = WorkspaceStore(projects)
    return projects, project, workspace


def _dataset(dataset_id: str | None = None) -> dict:
    dataset_id = dataset_id or str(uuid.uuid4())
    return {
        "id": dataset_id,
        "version": "0" * 64,
        "name": "Parcels",
        "kind": "vector",
        "source": {
            "path": "C:/input/parcels.geojson",
            "layer": "parcels",
            "driver": "GeoJSON",
            "fingerprint": "sha256:test",
            "encoding": None,
            "assignedCrs": None,
            "crsWkt": "GEOGCRS[test]",
            "metadata": {},
        },
        "relativePath": f"datasets/{dataset_id}.gpkg",
        "storageLayer": "features",
        "featureCount": 1,
        "geometryType": "Polygon",
        "crsWkt": "GEOGCRS[test]",
        "crsAuthority": "EPSG:4326",
        "bounds": [0.0, 0.0, 1.0, 1.0],
        "boundsWgs84": [0.0, 0.0, 1.0, 1.0],
        "fields": [
            {
                "name": "code",
                "sourceType": "String",
                "storageType": "TEXT",
                "nullable": True,
                "alias": None,
                "width": None,
                "precision": None,
                "metadataStatus": "partial",
            }
        ],
        "internalIdField": "_sad_id",
        "sourceFidField": "_sad_source_fid",
        "report": {
            "status": "warning",
            "checks": [],
            "warnings": [],
            "notChecked": [],
            "counts": {"features": 1},
            "validatorVersion": "test",
        },
        "createdAt": "2026-09-09T00:00:00.000000Z",
    }


def _table_dataset(dataset_id: str | None = None) -> dict:
    dataset = _dataset(dataset_id)
    dataset.update(
        {
            "kind": "table",
            "source": {
                **dataset["source"],
                "driver": "CSV",
                "encoding": "utf-8",
                "crsWkt": None,
            },
            "storageLayer": "records",
            "geometryType": None,
            "crsWkt": None,
            "crsAuthority": None,
            "bounds": None,
            "boundsWgs84": None,
            "cellMetadataLayer": None,
        }
    )
    return dataset


def _stage_import(project: dict, workspace: WorkspaceStore, dataset: dict, task_id: str) -> Path:
    root = Path(project["projectPath"]).parent
    staged = root / "staging" / "tasks" / task_id / "snapshot.gpkg"
    staged.parent.mkdir(parents=True)
    content = b"validated snapshot"
    staged.write_bytes(content)
    dataset["version"] = hashlib.sha256(content).hexdigest()
    workspace.create_task("import", task_id=task_id, dataset_id=dataset["id"])
    workspace.prepare_import_publication(task_id, dataset, staged)
    return staged


def test_dataset_publication_persists_and_rows_are_immutable(tmp_path: Path) -> None:
    projects, project, workspace = _stores(tmp_path)
    dataset = _dataset()
    task_id = str(uuid.uuid4())
    _stage_import(project, workspace, dataset, task_id)

    published = workspace.complete_import_publication(task_id)
    assert published["status"] == "completed"
    snapshot = workspace.get({"path": project["projectPath"]})
    assert snapshot["datasets"] == [dataset]
    assert snapshot["layers"][0]["datasetId"] == dataset["id"]

    with sqlite3.connect(project["projectPath"]) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE datasets SET version = 'changed'")

    projects.close()
    reopened = ProjectStore()
    reopened.open({"path": project["projectPath"]})
    assert WorkspaceStore(reopened).get({"path": project["projectPath"]})["datasets"] == [dataset]
    reopened.close()


def test_layer_updates_reorder_and_remove_retain_dataset(tmp_path: Path) -> None:
    projects, project, workspace = _stores(tmp_path)
    datasets = [_dataset(), _dataset()]
    for dataset in datasets:
        task_id = str(uuid.uuid4())
        _stage_import(project, workspace, dataset, task_id)
        workspace.complete_import_publication(task_id)
    layers = workspace.get({"path": project["projectPath"]})["layers"]

    updated = workspace.update_layer(
        {
            "path": project["projectPath"],
            "layerId": layers[0]["id"],
            "changes": {"name": "Styled", "opacity": 0.5, "categoryField": "code", "color": "#123ABC"},
        }
    )
    assert updated["name"] == "Styled"
    assert updated["opacity"] == 0.5
    reordered = workspace.reorder_layers(
        {"path": project["projectPath"], "layerIds": [layers[1]["id"], layers[0]["id"]]}
    )
    assert [layer["id"] for layer in reordered] == [layers[1]["id"], layers[0]["id"]]
    with pytest.raises(InvalidParamsError):
        workspace.reorder_layers({"path": project["projectPath"], "layerIds": [layers[0]["id"]]})

    assert workspace.remove_layer({"path": project["projectPath"], "layerId": layers[0]["id"]}) == {
        "removed": True
    }
    snapshot = workspace.get({"path": project["projectPath"]})
    assert len(snapshot["datasets"]) == 2
    assert len(snapshot["layers"]) == 1
    projects.close()


def test_table_publication_uses_generic_registry_without_map_layer(tmp_path: Path) -> None:
    projects, project, workspace = _stores(tmp_path)
    dataset = _table_dataset()
    task_id = str(uuid.uuid4())
    _stage_import(project, workspace, dataset, task_id)

    completed = workspace.complete_import_publication(task_id)
    snapshot = workspace.get({"path": project["projectPath"]})

    assert completed["status"] == "completed"
    assert snapshot["datasets"] == [dataset]
    assert snapshot["layers"] == []
    projects.close()


def test_points_task_only_publishes_vector_dataset_and_layer(tmp_path: Path) -> None:
    projects, project, workspace = _stores(tmp_path)
    vector = _dataset()
    task_id = str(uuid.uuid4())
    root = Path(project["projectPath"]).parent
    staged = root / "staging" / "tasks" / task_id / "snapshot.gpkg"
    staged.parent.mkdir(parents=True)
    content = b"point snapshot"
    staged.write_bytes(content)
    vector["version"] = hashlib.sha256(content).hexdigest()
    workspace.create_task("points", task_id=task_id, dataset_id=vector["id"])
    workspace.prepare_import_publication(task_id, vector, staged)

    assert workspace.complete_import_publication(task_id)["kind"] == "points"
    assert workspace.get({"path": project["projectPath"]})["layers"][0]["datasetId"] == vector["id"]

    invalid_task = workspace.create_task("points", dataset_id=str(uuid.uuid4()))
    invalid_table = _table_dataset(invalid_task["datasetId"])
    invalid_staged = root / "staging" / "tasks" / invalid_task["id"] / "snapshot.gpkg"
    invalid_staged.parent.mkdir(parents=True)
    invalid_staged.write_bytes(b"table")
    invalid_table["version"] = hashlib.sha256(b"table").hexdigest()
    with pytest.raises(DomainError) as error:
        workspace.prepare_import_publication(invalid_task["id"], invalid_table, invalid_staged)
    assert error.value.kind == "invalid_worker_result"
    projects.close()


def test_pending_import_is_recovered_and_running_tasks_are_interrupted(tmp_path: Path) -> None:
    projects, project, workspace = _stores(tmp_path)
    dataset = _dataset()
    task_id = str(uuid.uuid4())
    staged = _stage_import(project, workspace, dataset, task_id)
    other = workspace.create_task("export", destination=str(tmp_path / "out.gpkg"))

    fresh = WorkspaceStore(workspace.projects)
    snapshot = fresh.get({"path": project["projectPath"]})

    assert snapshot["datasets"] == [dataset]
    tasks = {task["id"]: task for task in snapshot["tasks"]}
    assert tasks[task_id]["status"] == "completed"
    assert tasks[other["id"]]["status"] == "interrupted"
    assert not staged.parent.exists()
    projects.close()


def test_publication_never_overwrites_existing_dataset(tmp_path: Path) -> None:
    projects, project, workspace = _stores(tmp_path)
    dataset = _dataset()
    task_id = str(uuid.uuid4())
    _stage_import(project, workspace, dataset, task_id)
    final = Path(project["projectPath"]).parent / dataset["relativePath"]
    final.write_bytes(b"unrelated existing file")

    with pytest.raises(DomainError) as error:
        workspace.complete_import_publication(task_id)

    assert error.value.kind == "publication_conflict"
    assert final.read_bytes() == b"unrelated existing file"
    projects.close()


def test_import_journal_survives_registration_failure_and_recovers_on_reopen(tmp_path: Path) -> None:
    projects, project, workspace = _stores(tmp_path)
    dataset = _dataset()
    task_id = str(uuid.uuid4())
    root = Path(project["projectPath"]).parent
    work_dir = root / "staging" / "tasks" / task_id
    work_dir.mkdir(parents=True)
    staged = work_dir / "snapshot.gpkg"
    content = b"validated snapshot"
    staged.write_bytes(content)
    dataset["version"] = hashlib.sha256(content).hexdigest()
    workspace.create_task("import", task_id=task_id, dataset_id=dataset["id"])
    (work_dir / "result.json").write_text(
        json.dumps({"ok": True, "result": {"dataset": dataset, "artifactPath": str(staged)}}), encoding="utf-8"
    )
    with sqlite3.connect(project["projectPath"]) as connection:
        connection.execute(
            "CREATE TRIGGER fail_registration BEFORE INSERT ON datasets "
            "BEGIN SELECT RAISE(ABORT, 'injected registration failure'); END"
        )

    manager = TaskManager(projects, workspace)
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=5)
    manager._process = process
    manager._task_id = task_id
    manager._work_dir = work_dir
    manager.harvest()

    final = Path(project["projectPath"]).parent / dataset["relativePath"]
    assert final.is_file() and not staged.exists()
    assert workspace.has_pending(task_id)
    interrupted = workspace.task(task_id)
    assert interrupted["status"] == "interrupted"
    assert interrupted["stage"] == "publication_pending"
    assert work_dir.exists()
    with sqlite3.connect(project["projectPath"]) as connection:
        connection.execute("DROP TRIGGER fail_registration")
    projects.close()

    reopened = ProjectStore()
    reopened.open({"path": project["projectPath"]})
    recovered = WorkspaceStore(reopened).get({"path": project["projectPath"]})
    assert recovered["datasets"] == [dataset]
    assert next(task for task in recovered["tasks"] if task["id"] == task_id)["status"] == "completed"
    assert not work_dir.exists()
    reopened.close()


def test_export_journal_reconciles_destination_after_commit_failure(tmp_path: Path) -> None:
    projects, project, workspace = _stores(tmp_path)
    destination = (tmp_path / "published.gpkg").resolve()
    task = workspace.create_task("export", destination=str(destination))
    temporary = destination.with_name(f".{destination.name}.{task['id']}.pending")
    content = b"verified export"
    temporary.write_bytes(content)
    workspace.prepare_export_publication(task["id"], temporary)
    with sqlite3.connect(project["projectPath"]) as connection:
        connection.execute(
            "CREATE TRIGGER fail_task_completion BEFORE UPDATE OF status ON tasks "
            "WHEN NEW.status = 'completed' BEGIN SELECT RAISE(ABORT, 'injected completion failure'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected completion failure"):
        workspace.complete_export_publication(task["id"])

    assert destination.read_bytes() == content
    assert not temporary.exists()
    assert workspace.has_pending(task["id"])
    with sqlite3.connect(project["projectPath"]) as connection:
        connection.execute("DROP TRIGGER fail_task_completion")
    projects.close()

    reopened = ProjectStore()
    reopened.open({"path": project["projectPath"]})
    recovered = WorkspaceStore(reopened).get({"path": project["projectPath"]})
    assert next(item for item in recovered["tasks"] if item["id"] == task["id"])["status"] == "completed"
    assert destination.read_bytes() == content
    reopened.close()


def test_reopen_cleans_unjournaled_terminal_export_staging(tmp_path: Path) -> None:
    projects, project, workspace = _stores(tmp_path)
    destination = str((tmp_path / "done.gpkg").resolve())
    task = workspace.create_task("export", destination=destination)
    workspace.update_task(task["id"], status="completed", stage="completed")
    work_dir = Path(project["projectPath"]).parent / "staging" / "tasks" / task["id"]
    work_dir.mkdir(parents=True)
    (work_dir / "export.gpkg").write_bytes(b"left after commit")

    fresh = WorkspaceStore(projects)
    fresh.get({"path": project["projectPath"]})

    assert not work_dir.exists()
    projects.close()


def test_managed_path_rejects_wrong_or_traversing_relative_path(tmp_path: Path) -> None:
    projects, _, workspace = _stores(tmp_path)
    dataset = _dataset()
    dataset["relativePath"] = "../outside.gpkg"
    with pytest.raises(DomainError) as error:
        workspace.managed_path(dataset)
    assert error.value.kind == "invalid_dataset"
    projects.close()


def test_task_cancel_reaps_a_real_child_process(tmp_path: Path) -> None:
    projects, project, workspace = _stores(tmp_path)

    def command(request_path: Path) -> list[str]:
        script = (
            "import json,sys,time; from pathlib import Path; "
            "p=Path(sys.argv[1]); w=p.parent; "
            "w.joinpath('progress.json').write_text(json.dumps({'stage':'waiting','completed':0,'total':1})); "
            "\nwhile not w.joinpath('cancel.flag').exists(): time.sleep(0.02); "
            "w.joinpath('result.json').write_text(json.dumps({'ok':False,'error':{'kind':'task_cancelled','message':'Cancelled','detail':None}}))"
        )
        return [sys.executable, "-c", script, str(request_path)]

    tasks = TaskManager(projects, workspace, command_factory=command)
    source = tmp_path / "source.geojson"
    source.write_text("{}", encoding="utf-8")
    task = tasks.start_import(
        {
            "path": project["projectPath"],
            "sourcePath": str(source),
            "sourceLayer": "features",
            "encoding": None,
            "assignedCrs": None,
        }
    )
    deadline = time.monotonic() + 3
    while task["stage"] != "waiting" and time.monotonic() < deadline:
        time.sleep(0.03)
        task = tasks.get({"path": project["projectPath"], "taskId": task["id"]})

    cancelled = tasks.cancel({"path": project["projectPath"], "taskId": task["id"]})
    assert cancelled["status"] == "cancelled"
    assert tasks._process is None
    assert not (Path(project["projectPath"]).parent / "staging" / "tasks" / task["id"]).exists()
    projects.close()


def test_table_and_points_tasks_write_private_protocol_two_requests(tmp_path: Path) -> None:
    projects, project, workspace = _stores(tmp_path)

    def command(request_path: Path) -> list[str]:
        script = (
            "import sys,time; from pathlib import Path; w=Path(sys.argv[1]).parent; "
            "\nwhile not w.joinpath('cancel.flag').exists(): time.sleep(0.02)"
        )
        return [sys.executable, "-c", script, str(request_path)]

    manager = TaskManager(projects, workspace, command_factory=command)
    source = tmp_path / "coordinates.csv"
    source.write_text("x,y\n114,27\n", encoding="utf-8")
    imported = manager.start_table_import(
        {
            "path": project["projectPath"], "sourcePath": str(source), "encoding": "UTF-8",
            "delimiter": ",", "sheet": None, "headerRow": 1,
        }
    )
    import_request = json.loads(
        (Path(project["projectPath"]).parent / "staging" / "tasks" / imported["id"] / "request.json").read_text()
    )
    assert imported["kind"] == "import"
    assert import_request["protocolVersion"] == 2
    assert import_request["kind"] == "table_import"
    assert import_request["payload"]["datasetId"] == imported["datasetId"]
    manager.cancel({"path": project["projectPath"], "taskId": imported["id"]})

    table = _table_dataset()
    table["fields"].append({**table["fields"][0], "name": "name"})
    publication_id = str(uuid.uuid4())
    _stage_import(project, workspace, table, publication_id)
    workspace.complete_import_publication(publication_id)
    points = manager.start_table_points(
        {
            "path": project["projectPath"], "datasetId": table["id"], "xField": "code",
            "yField": "name", "declaredCrs": "EPSG:4326",
        }
    )
    points_request = json.loads(
        (Path(project["projectPath"]).parent / "staging" / "tasks" / points["id"] / "request.json").read_text()
    )
    assert points["kind"] == "points"
    assert points_request["protocolVersion"] == 2
    assert points_request["kind"] == "points"
    assert points_request["payload"]["dataset"]["id"] == table["id"]
    assert points_request["payload"]["datasetId"] == points["datasetId"]
    manager.cancel({"path": project["projectPath"], "taskId": points["id"]})
    projects.close()


def test_table_import_and_points_complete_through_real_worker(tmp_path: Path) -> None:
    projects, project, workspace = _stores(tmp_path)
    manager = TaskManager(projects, workspace)
    source = tmp_path / "coordinates.csv"
    source.write_text("x,y,name\n114,27,one\n,27,missing x\n", encoding="utf-8")

    imported = manager.start_table_import(
        {
            "path": project["projectPath"], "sourcePath": str(source), "encoding": "UTF-8",
            "delimiter": ",", "sheet": None, "headerRow": 1,
        }
    )
    deadline = time.monotonic() + 15
    while imported["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
        imported = manager.get({"path": project["projectPath"], "taskId": imported["id"]})
    assert imported["status"] == "completed", imported

    table = workspace.dataset(project["projectPath"], imported["datasetId"])
    assert table["kind"] == "table"
    assert workspace.get({"path": project["projectPath"]})["layers"] == []

    points = manager.start_table_points(
        {
            "path": project["projectPath"], "datasetId": table["id"], "xField": "x",
            "yField": "y", "declaredCrs": "EPSG:4326",
        }
    )
    deadline = time.monotonic() + 15
    while points["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
        points = manager.get({"path": project["projectPath"], "taskId": points["id"]})
    assert points["status"] == "completed", points

    snapshot = workspace.get({"path": project["projectPath"]})
    datasets = {dataset["id"]: dataset for dataset in snapshot["datasets"]}
    assert datasets[points["datasetId"]]["kind"] == "vector"
    assert datasets[points["datasetId"]]["report"]["counts"]["invalidCoordinates"] == 1
    assert [layer["datasetId"] for layer in snapshot["layers"]] == [points["datasetId"]]
    manager.close()
    projects.close()


def test_worker_rejects_malformed_request_without_stdout(tmp_path: Path, capsys) -> None:
    from spatial_engine.worker import run_worker

    request = tmp_path / "request.json"
    request.write_text(json.dumps({"kind": "unsupported"}), encoding="utf-8")
    assert run_worker(request) != 0
    assert capsys.readouterr().out == ""


def test_worker_does_not_delete_colliding_export_pending_file(tmp_path: Path, monkeypatch) -> None:
    from spatial_engine import vectors
    from spatial_engine.worker import run_worker

    task_id = str(uuid.uuid4())
    work_dir = tmp_path / task_id
    work_dir.mkdir()
    artifact = work_dir / "export.gpkg"
    artifact.write_bytes(b"worker export")
    pending = tmp_path / f".out.gpkg.{task_id}.pending"
    pending.write_bytes(b"unrelated")
    monkeypatch.setattr(
        vectors,
        "export_vector",
        lambda payload, task_dir, progress, cancelled: {"artifactPath": str(artifact)},
    )
    request = work_dir / "request.json"
    request.write_text(
        json.dumps(
            {
                "protocolVersion": 2,
                "taskId": task_id,
                "kind": "export",
                "payload": {},
                "workDir": str(work_dir),
                "publishPath": str(pending),
            }
        ),
        encoding="utf-8",
    )

    assert run_worker(request) != 0
    assert pending.read_bytes() == b"unrelated"


def test_worker_retries_transient_windows_progress_replace(tmp_path: Path, monkeypatch) -> None:
    from spatial_engine import vectors, worker

    task_id = str(uuid.uuid4())
    work_dir = tmp_path / task_id
    work_dir.mkdir()
    artifact = work_dir / "snapshot.gpkg"

    def fake_import(payload, task_dir, progress, cancelled):
        progress("reading", 1, 1)
        artifact.write_bytes(b"snapshot")
        return {"dataset": {}, "artifactPath": str(artifact)}

    monkeypatch.setattr(vectors, "import_vector", fake_import)
    real_replace = worker.os.replace
    attempts = 0

    def transient_replace(source, destination):
        nonlocal attempts
        if Path(destination).name == "progress.json" and attempts < 2:
            attempts += 1
            raise PermissionError(13, "simulated Windows sharing violation", str(destination))
        attempts += 1
        return real_replace(source, destination)

    monkeypatch.setattr(worker.os, "replace", transient_replace)
    request = work_dir / "request.json"
    request.write_text(
        json.dumps(
            {
                "protocolVersion": 2,
                "taskId": task_id,
                "kind": "import",
                "payload": {},
                "workDir": str(work_dir),
                "publishPath": None,
            }
        ),
        encoding="utf-8",
    )

    assert worker.run_worker(request) == 0
    assert attempts >= 3
    assert json.loads((work_dir / "progress.json").read_text(encoding="utf-8"))["stage"] == "reading"
