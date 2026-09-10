from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import spatial_engine.projects as project_module
from spatial_engine.errors import DomainError, InvalidParamsError
from spatial_engine.projects import APPLICATION_ID, SCHEMA_VERSION
from spatial_engine.rpc import Engine


def valid_save(project: dict, **overrides) -> dict:
    params = {
        "path": project["projectPath"],
        "name": "Updated project",
        "description": "Saved through the engine",
        "analysisCrs": "EPSG:4547",
        "displayCrs": "EPSG:3857",
        "viewState": {"center": [114.2, 27.3], "zoom": 8},
    }
    params.update(overrides)
    return params


def test_project_create_save_close_open_round_trip(tmp_path: Path) -> None:
    engine = Engine()
    project_dir = tmp_path / "含空格 project"

    created = engine.dispatch("project.create", {"directory": str(project_dir), "name": "Land study"})
    assert created["schemaVersion"] == 6
    assert created["name"] == "Land study"
    assert created["description"] == ""
    assert created["analysisCrs"] is None
    assert created["displayCrs"] == "EPSG:3857"
    assert created["viewState"] == {"center": [114.0, 27.1], "zoom": 5.0}
    assert set(path.name for path in project_dir.iterdir()) >= {
        "project.spa", "datasets", "rasters", "results", "staging", "cache", "backups"
    }

    with sqlite3.connect(project_dir / "project.spa") as connection:
        assert connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION

    saved = engine.dispatch("project.save", valid_save(created))
    assert saved["id"] == created["id"]
    assert saved["createdAt"] == created["createdAt"]
    assert saved["updatedAt"] >= created["updatedAt"]
    assert saved["description"] == "Saved through the engine"
    assert engine.dispatch("project.close", {}) == {"closed": True}

    reopened = engine.dispatch("project.open", {"path": created["projectPath"]})
    assert reopened == saved


def test_create_never_overwrites_existing_project(tmp_path: Path) -> None:
    first = Engine()
    project_dir = tmp_path / "existing"
    created = first.dispatch("project.create", {"directory": str(project_dir), "name": "Original"})
    original_bytes = Path(created["projectPath"]).read_bytes()

    with pytest.raises(DomainError) as error:
        Engine().dispatch("project.create", {"directory": str(project_dir), "name": "Replacement"})

    assert error.value.kind == "project_exists"
    assert Path(created["projectPath"]).read_bytes() == original_bytes
    first.close()


def test_create_collision_after_initial_check_preserves_other_project(tmp_path: Path, monkeypatch) -> None:
    seed_engine = Engine()
    seed = seed_engine.dispatch("project.create", {"directory": str(tmp_path / "seed"), "name": "Other"})
    seed_engine.close()
    seed_bytes = Path(seed["projectPath"]).read_bytes()
    target_path = tmp_path / "race" / "project.spa"
    acquire_lock = project_module._acquire_lock

    def create_other_then_lock(path: Path):
        path.write_bytes(seed_bytes)
        return acquire_lock(path)

    monkeypatch.setattr(project_module, "_acquire_lock", create_other_then_lock)
    with pytest.raises(DomainError) as error:
        Engine().dispatch("project.create", {"directory": str(target_path.parent), "name": "Mine"})

    assert error.value.kind == "project_exists"
    assert target_path.read_bytes() == seed_bytes


def test_create_failure_closes_database_before_cleanup_and_allows_retry(tmp_path: Path, monkeypatch) -> None:
    project_dir = tmp_path / "retry"
    project_path = project_dir / "project.spa"
    real_connect = project_module.sqlite3.connect
    injected = False

    class FailingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            nonlocal injected
            if not injected and sql.startswith("INSERT INTO project_metadata"):
                injected = True
                raise sqlite3.OperationalError("injected create failure")
            return super().execute(sql, parameters)

    def connect_with_failure(database, *args, **kwargs):
        if Path(database) == project_path and not injected:
            return real_connect(database, *args, factory=FailingConnection, **kwargs)
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(project_module.sqlite3, "connect", connect_with_failure)
    engine = Engine()
    with pytest.raises(sqlite3.OperationalError, match="injected create failure"):
        engine.dispatch("project.create", {"directory": str(project_dir), "name": "First attempt"})

    assert not project_path.exists()
    retried = engine.dispatch("project.create", {"directory": str(project_dir), "name": "Retry"})
    assert retried["name"] == "Retry"
    engine.close()


def test_project_lock_is_exclusive_and_released_on_close(tmp_path: Path) -> None:
    owner = Engine()
    contender = Engine()
    created = owner.dispatch("project.create", {"directory": str(tmp_path / "locked"), "name": "Locked"})

    with pytest.raises(DomainError) as error:
        contender.dispatch("project.open", {"path": created["projectPath"]})
    assert error.value.kind == "project_locked"

    owner.dispatch("project.close", {})
    assert contender.dispatch("project.open", {"path": created["projectPath"]})["id"] == created["id"]
    contender.close()


def test_invalid_save_is_atomic_and_active_project_survives_failed_open(tmp_path: Path) -> None:
    engine = Engine()
    created = engine.dispatch("project.create", {"directory": str(tmp_path / "valid"), "name": "Original"})

    with pytest.raises(InvalidParamsError):
        engine.dispatch("project.save", valid_save(created, viewState={"center": [float("inf"), 27.3], "zoom": 8}))

    corrupt = tmp_path / "corrupt.spa"
    corrupt.write_text("not sqlite", encoding="utf-8")
    with pytest.raises(DomainError) as error:
        engine.dispatch("project.open", {"path": str(corrupt)})
    assert error.value.kind == "invalid_project"

    saved = engine.dispatch("project.save", valid_save(created, name="Still active"))
    assert saved["name"] == "Still active"
    engine.dispatch("project.close", {})
    assert Engine().dispatch("project.open", {"path": created["projectPath"]})["name"] == "Still active"


def test_open_rejects_future_schema_without_modifying_file(tmp_path: Path) -> None:
    creator = Engine()
    created = creator.dispatch("project.create", {"directory": str(tmp_path / "future"), "name": "Future"})
    creator.close()
    project_path = Path(created["projectPath"])
    with sqlite3.connect(project_path) as connection:
        connection.execute("PRAGMA user_version = 999")
    before = project_path.read_bytes()

    with pytest.raises(DomainError) as error:
        Engine().dispatch("project.open", {"path": str(project_path)})

    assert error.value.kind == "unsupported_schema"
    assert project_path.read_bytes() == before


def test_open_migrates_v1_after_creating_a_valid_backup(tmp_path: Path) -> None:
    creator = Engine()
    created = creator.dispatch("project.create", {"directory": str(tmp_path / "legacy"), "name": "Legacy"})
    creator.close()
    project_path = Path(created["projectPath"])
    with sqlite3.connect(project_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DROP TRIGGER IF EXISTS datasets_no_update")
        connection.execute("DROP TRIGGER IF EXISTS datasets_no_delete")
        connection.execute("DROP TABLE IF EXISTS pending_exports")
        connection.execute("DROP TRIGGER IF EXISTS vector_datasets_no_update")
        connection.execute("DROP TRIGGER IF EXISTS vector_datasets_no_delete")
        connection.execute("DROP TABLE IF EXISTS pending_publications")
        connection.execute("DROP TABLE IF EXISTS map_layers")
        connection.execute("DROP TABLE IF EXISTS datasets")
        connection.execute("DROP TABLE IF EXISTS vector_datasets")
        connection.execute("DROP TABLE IF EXISTS tasks")
        connection.execute("PRAGMA user_version = 1")

    opened = Engine().dispatch("project.open", {"path": str(project_path)})

    assert opened["schemaVersion"] == 6
    backups = list((project_path.parent / "backups").glob("*.spa"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
        assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_failed_migration_rolls_back_releases_lock_and_keeps_active_project(
    tmp_path: Path, monkeypatch
) -> None:
    engine = Engine()
    active = engine.dispatch("project.create", {"directory": str(tmp_path / "active"), "name": "Active"})
    legacy_creator = Engine()
    legacy = legacy_creator.dispatch("project.create", {"directory": str(tmp_path / "legacy"), "name": "Legacy"})
    legacy_creator.close()
    legacy_path = Path(legacy["projectPath"])
    with sqlite3.connect(legacy_path) as connection:
        connection.execute("PRAGMA user_version = 1")

    real_migrate = project_module._migrate_to_current

    def fail_migration(path: Path, source_version: int) -> None:
        raise sqlite3.OperationalError("injected migration failure")

    monkeypatch.setattr(project_module, "_migrate_to_current", fail_migration)
    with pytest.raises(DomainError) as error:
        engine.dispatch("project.open", {"path": str(legacy_path)})
    assert error.value.kind == "project_migration_failed"
    assert engine.dispatch("project.save", valid_save(active, name="Still active"))["name"] == "Still active"
    with sqlite3.connect(legacy_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1

    monkeypatch.setattr(project_module, "_migrate_to_current", real_migrate)
    contender = Engine()
    assert contender.dispatch("project.open", {"path": str(legacy_path)})["schemaVersion"] == 6
    contender.close()


def test_open_migrates_v2_registry_tasks_and_layers_with_v2_backup(tmp_path: Path) -> None:
    creator = Engine()
    created = creator.dispatch("project.create", {"directory": str(tmp_path / "v2"), "name": "Version two"})
    creator.close()
    project_path = Path(created["projectPath"])
    dataset_id = "00000000-0000-0000-0000-000000000001"
    layer_id = "00000000-0000-0000-0000-000000000002"
    task_id = "00000000-0000-0000-0000-000000000003"
    export_task_id = "00000000-0000-0000-0000-000000000004"
    with sqlite3.connect(project_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DROP TRIGGER datasets_no_update")
        connection.execute("DROP TRIGGER datasets_no_delete")
        connection.execute("DROP TABLE pending_publications")
        connection.execute("DROP TABLE pending_exports")
        connection.execute("DROP TABLE map_layers")
        connection.execute("DROP TABLE datasets")
        connection.execute("DROP TABLE tasks")
        project_module._create_v2_schema(connection)
        connection.execute(
            "INSERT INTO vector_datasets VALUES (?, ?, ?, ?, ?)",
            (dataset_id, "0" * 64, f"datasets/{dataset_id}.gpkg", '{"kind":"vector"}', "2026-09-09T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO map_layers VALUES (?, ?, 'Layer', 1, 1.0, '#123456', NULL, '{}', 0)",
            (layer_id, dataset_id),
        )
        connection.execute(
            "INSERT INTO tasks VALUES (?, 'import', 'running', 'publishing', 1, 1, ?, ?, ?, NULL, NULL)",
            (task_id, "2026-09-09T00:00:00Z", "2026-09-09T00:00:01Z", dataset_id),
        )
        connection.execute(
            "INSERT INTO tasks VALUES (?, 'export', 'running', 'publishing', 1, 1, ?, ?, ?, ?, NULL)",
            (
                export_task_id, "2026-09-09T00:00:02Z", "2026-09-09T00:00:03Z", dataset_id,
                str(tmp_path / "export.gpkg"),
            ),
        )
        connection.execute(
            "INSERT INTO pending_publications VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, '{"kind":"vector"}', f"staging/tasks/{task_id}/snapshot.gpkg", f"datasets/{dataset_id}.gpkg", 1, "0" * 64),
        )
        connection.execute(
            "INSERT INTO pending_exports VALUES (?, ?, ?, ?, ?)",
            (export_task_id, str(tmp_path / ".export.pending"), str(tmp_path / "export.gpkg"), 1, "1" * 64),
        )
        connection.execute("PRAGMA user_version = 2")

    opener = Engine()
    opened = opener.dispatch("project.open", {"path": str(project_path)})
    assert opened["schemaVersion"] == 6
    with sqlite3.connect(project_path) as connection:
        assert connection.execute("SELECT dataset_id FROM datasets").fetchone()[0] == dataset_id
        assert connection.execute("SELECT layer_id FROM map_layers").fetchone()[0] == layer_id
        assert connection.execute("SELECT task_id, kind FROM tasks ORDER BY task_id").fetchall() == [
            (task_id, "import"), (export_task_id, "export")
        ]
        assert connection.execute("SELECT task_id FROM pending_publications").fetchone()[0] == task_id
        assert connection.execute("SELECT task_id FROM pending_exports").fetchone()[0] == export_task_id
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    backups = list((project_path.parent / "backups").glob("project-v2-*.spa"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 2
        assert backup.execute("SELECT dataset_id FROM vector_datasets").fetchone()[0] == dataset_id
    opener.close()


def test_open_migrates_v3_datasets_tasks_layers_and_journals_with_backup(tmp_path: Path) -> None:
    creator = Engine()
    created = creator.dispatch("project.create", {"directory": str(tmp_path / "v3"), "name": "Version three"})
    creator.close()
    project_path = Path(created["projectPath"])
    vector_id = "00000000-0000-0000-0000-000000000011"
    table_id = "00000000-0000-0000-0000-000000000012"
    layer_id = "00000000-0000-0000-0000-000000000013"
    import_task = "00000000-0000-0000-0000-000000000014"
    export_task = "00000000-0000-0000-0000-000000000015"
    points_task = "00000000-0000-0000-0000-000000000016"
    with sqlite3.connect(project_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DROP TABLE map_layers")
        project_module._create_v3_schema(connection)
        connection.executemany(
            "INSERT INTO datasets VALUES (?, ?, ?, ?, ?)",
            [
                (vector_id, "0" * 64, f"datasets/{vector_id}.gpkg", '{"kind":"vector"}', "2026-09-09T00:00:00Z"),
                (table_id, "1" * 64, f"datasets/{table_id}.gpkg", '{"kind":"table"}', "2026-09-09T00:00:01Z"),
            ],
        )
        connection.execute(
            "INSERT INTO map_layers VALUES (?, ?, 'Vector', 1, 0.75, '#123456', 'code', '{\"A\":\"#ABCDEF\"}', 0)",
            (layer_id, vector_id),
        )
        rows = [
            (import_task, "import", table_id, None),
            (export_task, "export", vector_id, str(tmp_path / "export.gpkg")),
            (points_task, "points", vector_id, None),
        ]
        for task_id, kind, dataset_id, destination in rows:
            connection.execute(
                "INSERT INTO tasks VALUES (?, ?, 'running', 'publishing', 1, 1, ?, ?, ?, ?, NULL)",
                (task_id, kind, "2026-09-09T00:00:02Z", "2026-09-09T00:00:03Z", dataset_id, destination),
            )
        connection.execute(
            "INSERT INTO pending_publications VALUES (?, ?, ?, ?, ?, ?)",
            (import_task, '{"kind":"table"}', f"staging/tasks/{import_task}/snapshot.gpkg", f"datasets/{table_id}.gpkg", 1, "0" * 64),
        )
        connection.execute(
            "INSERT INTO pending_exports VALUES (?, ?, ?, ?, ?)",
            (export_task, str(tmp_path / ".export.pending"), str(tmp_path / "export.gpkg"), 1, "1" * 64),
        )
        connection.execute("PRAGMA user_version = 3")

    opener = Engine()
    opened = opener.dispatch("project.open", {"path": str(project_path)})

    assert opened["schemaVersion"] == 6
    with sqlite3.connect(project_path) as connection:
        assert connection.execute("SELECT dataset_id FROM datasets ORDER BY dataset_id").fetchall() == [(vector_id,), (table_id,)]
        assert connection.execute("SELECT layer_id, raster_style FROM map_layers").fetchone() == (layer_id, None)
        assert connection.execute("SELECT task_id, kind FROM tasks ORDER BY task_id").fetchall() == [
            (import_task, "import"), (export_task, "export"), (points_task, "points")
        ]
        assert connection.execute("SELECT task_id FROM pending_publications").fetchone() == (import_task,)
        assert connection.execute("SELECT task_id FROM pending_exports").fetchone() == (export_task,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    backups = list((project_path.parent / "backups").glob("project-v3-*.spa"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA user_version").fetchone() == (3,)
        assert {row[1] for row in backup.execute("PRAGMA table_info(map_layers)")} == {
            "layer_id", "dataset_id", "name", "visible", "opacity", "color", "category_field",
            "category_colors", "display_order",
        }
    opener.close()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("created_at", "2026-09-09"),
        ("updated_at", "2026-09-09T12:00:00"),
        ("updated_at", "2020-01-01T00:00:00.000000Z"),
    ],
)
def test_open_rejects_naive_or_out_of_order_project_timestamps(
    tmp_path: Path, column: str, value: str
) -> None:
    creator = Engine()
    created = creator.dispatch("project.create", {"directory": str(tmp_path / column), "name": "Timestamp"})
    creator.close()
    connection = sqlite3.connect(created["projectPath"])
    try:
        connection.execute(f"UPDATE project_metadata SET {column} = ? WHERE singleton = 1", (value,))
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DomainError) as error:
        Engine().dispatch("project.open", {"path": created["projectPath"]})

    assert error.value.kind == "invalid_project"


def test_parameter_validation_rejects_unknown_keys_and_non_active_path(tmp_path: Path) -> None:
    engine = Engine()
    created = engine.dispatch("project.create", {"directory": str(tmp_path / "one"), "name": "One"})

    with pytest.raises(InvalidParamsError):
        engine.dispatch("project.create", {"directory": str(tmp_path / "two"), "name": "Two", "extra": True})
    with pytest.raises(DomainError) as error:
        engine.dispatch("project.save", valid_save(created, path=str(tmp_path / "other" / "project.spa")))
    assert error.value.kind == "project_not_active"
