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
    assert created["schemaVersion"] == 1
    assert created["name"] == "Land study"
    assert created["description"] == ""
    assert created["analysisCrs"] is None
    assert created["displayCrs"] == "EPSG:3857"
    assert created["viewState"] == {"center": [114.0, 27.1], "zoom": 5.0}
    assert set(path.name for path in project_dir.iterdir()) >= {
        "project.spa", "datasets", "rasters", "results", "staging", "cache"
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
