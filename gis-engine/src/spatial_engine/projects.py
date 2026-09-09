from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO

import portalocker

from .errors import DomainError, InvalidParamsError
from .validation import (
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    require_crs,
    require_exact_keys,
    require_path,
    require_string,
    require_view_state,
)

APPLICATION_ID = 0x53504131
SCHEMA_VERSION = 2
PROJECT_IDENTITY = "spatial-analysis-desktop-project"
PROJECT_FILENAME = "project.spa"
OWNED_DIRECTORIES = ("datasets", "rasters", "results", "staging", "cache", "backups")


def _utc_now(after: str | None = None) -> str:
    now = datetime.now(UTC)
    if after:
        prior = datetime.fromisoformat(after.replace("Z", "+00:00"))
        if now <= prior:
            now = prior + timedelta(microseconds=1)
    return now.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(str(first.resolve(strict=False))) == os.path.normcase(str(second.resolve(strict=False)))


def _connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)


def _row_to_project(row: sqlite3.Row, path: Path, schema_version: int) -> dict[str, Any]:
    try:
        view_state = json.loads(row["view_state"])
    except (json.JSONDecodeError, TypeError) as exc:
        raise DomainError("Project metadata is corrupt", kind="invalid_project", detail="invalid view state") from exc
    return {
        "id": row["project_id"],
        "name": row["name"],
        "description": row["description"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "schemaVersion": schema_version,
        "projectPath": str(path.resolve()),
        "analysisCrs": row["analysis_crs"],
        "displayCrs": row["display_crs"],
        "viewState": view_state,
    }


def _read_project(path: Path, *, accepted_versions: frozenset[int] | None = None) -> dict[str, Any]:
    if not path.is_file():
        raise DomainError("Project file does not exist", kind="project_not_found", detail=str(path))
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect_readonly(path)
        connection.row_factory = sqlite3.Row
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        if schema_version > SCHEMA_VERSION:
            raise DomainError(
                "Project was created by a newer engine version",
                kind="unsupported_schema",
                detail=f"schema version {schema_version}",
            )
        accepted = accepted_versions or frozenset({SCHEMA_VERSION})
        if application_id != APPLICATION_ID or schema_version not in accepted:
            raise DomainError("File is not a Spatial Analysis project", kind="invalid_project")
        if schema_version == SCHEMA_VERSION:
            _validate_v2_schema(connection)
        row = connection.execute(
            "SELECT identity, project_id, name, description, created_at, updated_at, "
            "analysis_crs, display_crs, view_state FROM project_metadata WHERE singleton = 1"
        ).fetchone()
        if row is None or row["identity"] != PROJECT_IDENTITY:
            raise DomainError("Project identity is invalid", kind="invalid_project")
        project = _row_to_project(row, path, schema_version)
        _validate_stored_project(project)
        return project
    except DomainError:
        raise
    except (sqlite3.DatabaseError, OSError, ValueError, TypeError) as exc:
        raise DomainError("Project file is corrupt or unreadable", kind="invalid_project", detail=str(exc)) from exc
    finally:
        if connection is not None:
            connection.close()


def _parse_project_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{label} must include a UTC offset")
    return timestamp


def _validate_stored_project(project: dict[str, Any]) -> None:
    try:
        require_string(project["id"], "project id", maximum=64)
        uuid.UUID(project["id"])
        require_string(project["name"], "project name", maximum=MAX_NAME_LENGTH)
        require_string(project["description"], "project description", maximum=MAX_DESCRIPTION_LENGTH, allow_empty=True)
        require_crs(project["analysisCrs"], "analysis CRS", nullable=True)
        require_crs(project["displayCrs"], "display CRS")
        require_view_state(project["viewState"])
        created_at = _parse_project_timestamp(project["createdAt"], "createdAt")
        updated_at = _parse_project_timestamp(project["updatedAt"], "updatedAt")
        if updated_at < created_at:
            raise ValueError("updatedAt must not be earlier than createdAt")
    except (InvalidParamsError, ValueError, KeyError, TypeError) as exc:
        detail = exc.detail if isinstance(exc, InvalidParamsError) else str(exc)
        raise DomainError("Project metadata is corrupt", kind="invalid_project", detail=detail) from exc


def _create_v2_schema(connection: sqlite3.Connection) -> None:
    statements = (
        "CREATE TABLE IF NOT EXISTS vector_datasets ("
        "dataset_id TEXT PRIMARY KEY, version TEXT NOT NULL, relative_path TEXT NOT NULL UNIQUE, "
        "dataset_json TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS map_layers ("
        "layer_id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES vector_datasets(dataset_id), "
        "name TEXT NOT NULL, visible INTEGER NOT NULL CHECK (visible IN (0, 1)), "
        "opacity REAL NOT NULL CHECK (opacity >= 0 AND opacity <= 1), color TEXT NOT NULL, "
        "category_field TEXT, category_colors TEXT NOT NULL, display_order INTEGER NOT NULL UNIQUE)",
        "CREATE TABLE IF NOT EXISTS tasks ("
        "task_id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('import', 'export')), "
        "status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'cancelled', 'interrupted')), "
        "stage TEXT NOT NULL, completed INTEGER, total INTEGER, created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, dataset_id TEXT, destination TEXT, error TEXT)",
        "CREATE TABLE IF NOT EXISTS pending_publications ("
        "task_id TEXT PRIMARY KEY REFERENCES tasks(task_id), dataset_json TEXT NOT NULL, "
        "staged_relative_path TEXT NOT NULL, final_relative_path TEXT NOT NULL, "
        "artifact_size INTEGER NOT NULL, artifact_sha256 TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS pending_exports ("
        "task_id TEXT PRIMARY KEY REFERENCES tasks(task_id), temporary_path TEXT NOT NULL, "
        "destination_path TEXT NOT NULL, artifact_size INTEGER NOT NULL, artifact_sha256 TEXT NOT NULL)",
        "CREATE TRIGGER IF NOT EXISTS vector_datasets_no_update BEFORE UPDATE ON vector_datasets "
        "BEGIN SELECT RAISE(ABORT, 'vector datasets are immutable'); END",
        "CREATE TRIGGER IF NOT EXISTS vector_datasets_no_delete BEFORE DELETE ON vector_datasets "
        "BEGIN SELECT RAISE(ABORT, 'vector datasets are immutable'); END",
    )
    for statement in statements:
        connection.execute(statement)


def _validate_v2_schema(connection: sqlite3.Connection) -> None:
    expected_columns = {
        "vector_datasets": {"dataset_id", "version", "relative_path", "dataset_json", "created_at"},
        "map_layers": {
            "layer_id", "dataset_id", "name", "visible", "opacity", "color", "category_field",
            "category_colors", "display_order",
        },
        "tasks": {
            "task_id", "kind", "status", "stage", "completed", "total", "created_at", "updated_at",
            "dataset_id", "destination", "error",
        },
        "pending_publications": {
            "task_id", "dataset_json", "staged_relative_path", "final_relative_path", "artifact_size",
            "artifact_sha256",
        },
        "pending_exports": {
            "task_id", "temporary_path", "destination_path", "artifact_size", "artifact_sha256",
        },
    }
    for table, columns in expected_columns.items():
        actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if actual != columns:
            raise sqlite3.DatabaseError(f"project table {table} has an incompatible schema")
    triggers = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'vector_datasets'"
    )}
    if not {"vector_datasets_no_update", "vector_datasets_no_delete"} <= triggers:
        raise sqlite3.DatabaseError("project dataset immutability triggers are missing")


def _backup_v1_project(path: Path) -> Path:
    backup_directory = path.parent / "backups"
    backup_directory.mkdir(parents=True, exist_ok=True)
    backup_path = backup_directory / f"project-v1-{uuid.uuid4().hex}.spa"
    source: sqlite3.Connection | None = None
    target: sqlite3.Connection | None = None
    failure: Exception | None = None
    try:
        source = sqlite3.connect(path)
        target = sqlite3.connect(backup_path)
        source.backup(target)
    except Exception as exc:
        failure = exc
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
    if failure is not None:
        if backup_path.exists():
            try:
                backup_path.unlink()
            except OSError:
                pass
        raise failure
    _read_project(backup_path, accepted_versions=frozenset({1}))
    connection = _connect_readonly(backup_path)
    try:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("project backup failed integrity validation")
    finally:
        connection.close()
    return backup_path


def _migrate_v1_to_v2(path: Path) -> None:
    connection = sqlite3.connect(path, timeout=10.0)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        _create_v2_schema(connection)
        _validate_v2_schema(connection)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@dataclass
class ProjectSession:
    path: Path
    lock_path: Path
    lock_handle: BinaryIO

    def close(self) -> None:
        try:
            portalocker.unlock(self.lock_handle)
        finally:
            self.lock_handle.close()


def _acquire_lock(project_path: Path) -> ProjectSession:
    lock_path = project_path.with_name(f"{project_path.name}.lock")
    try:
        handle = lock_path.open("a+b")
        try:
            portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except Exception:
            handle.close()
            raise
    except (portalocker.LockException, OSError) as exc:
        raise DomainError("Project is already open in another engine", kind="project_locked", detail=str(project_path)) from exc
    return ProjectSession(path=project_path, lock_path=lock_path, lock_handle=handle)


class ProjectStore:
    def __init__(self) -> None:
        self._session: ProjectSession | None = None

    def create(self, params: dict[str, Any]) -> dict[str, Any]:
        require_exact_keys(params, {"directory", "name"})
        directory = require_path(params["directory"], "directory")
        name = require_string(params["name"], "name", maximum=MAX_NAME_LENGTH)
        if directory.exists() and not directory.is_dir():
            raise DomainError("Project directory is not a directory", kind="invalid_project_directory", detail=str(directory))
        project_path = directory / PROJECT_FILENAME
        if project_path.exists():
            raise DomainError("A project already exists in this directory", kind="project_exists", detail=str(project_path))

        directory_preexisted = directory.exists()
        created_subdirectories: list[Path] = []
        created_project_file = False
        session: ProjectSession | None = None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            session = _acquire_lock(project_path)
            try:
                project_path.open("xb").close()
                created_project_file = True
            except FileExistsError as exc:
                raise DomainError(
                    "A project already exists in this directory", kind="project_exists", detail=str(project_path)
                ) from exc
            created_at = _utc_now()
            project_id = str(uuid.uuid4())
            connection = sqlite3.connect(project_path)
            try:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                    connection.execute(
                        "CREATE TABLE project_metadata ("
                        "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
                        "identity TEXT NOT NULL, project_id TEXT NOT NULL UNIQUE, name TEXT NOT NULL, "
                        "description TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
                        "analysis_crs TEXT, display_crs TEXT NOT NULL, view_state TEXT NOT NULL)"
                    )
                    connection.execute(
                        "INSERT INTO project_metadata VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            PROJECT_IDENTITY,
                            project_id,
                            name,
                            "",
                            created_at,
                            created_at,
                            None,
                            "EPSG:3857",
                            json.dumps({"center": [114.0, 27.1], "zoom": 5.0}, separators=(",", ":")),
                        ),
                    )
                    _create_v2_schema(connection)
                    _validate_v2_schema(connection)
                    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            finally:
                connection.close()
            for child_name in OWNED_DIRECTORIES:
                child = directory / child_name
                if not child.exists():
                    child.mkdir()
                    created_subdirectories.append(child)
            project = _read_project(project_path)
        except Exception:
            if session:
                session.close()
            if created_project_file and project_path.exists():
                try:
                    project_path.unlink()
                except OSError:
                    pass
            for child in reversed(created_subdirectories):
                try:
                    child.rmdir()
                except OSError:
                    pass
            if not directory_preexisted:
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise

        self._replace_session(session)
        return project

    def open(self, params: dict[str, Any]) -> dict[str, Any]:
        require_exact_keys(params, {"path"})
        path = require_path(params["path"], "path")
        if self._session and _same_path(path, self._session.path):
            current = _read_project(path, accepted_versions=frozenset({1, SCHEMA_VERSION}))
            if current["schemaVersion"] == 1:
                try:
                    _backup_v1_project(path)
                    _migrate_v1_to_v2(path)
                except (sqlite3.DatabaseError, OSError) as exc:
                    raise DomainError(
                        "Could not migrate project", kind="project_migration_failed", detail=str(exc)
                    ) from exc
            return _read_project(path)
        _read_project(path, accepted_versions=frozenset({1, SCHEMA_VERSION}))
        session = _acquire_lock(path)
        try:
            candidate = _read_project(path, accepted_versions=frozenset({1, SCHEMA_VERSION}))
            if candidate["schemaVersion"] == 1:
                _backup_v1_project(path)
                _migrate_v1_to_v2(path)
            project = _read_project(path)
        except (sqlite3.DatabaseError, OSError) as exc:
            session.close()
            raise DomainError("Could not migrate project", kind="project_migration_failed", detail=str(exc)) from exc
        except Exception:
            session.close()
            raise
        self._replace_session(session)
        return project

    def active_path(self, path: str) -> Path:
        requested = require_path(path, "path")
        if self._session is None or not _same_path(requested, self._session.path):
            raise DomainError("Path is not the active project", kind="project_not_active", detail=str(requested))
        return self._session.path

    def save(self, params: dict[str, Any]) -> dict[str, Any]:
        require_exact_keys(
            params,
            {"path", "name", "description", "analysisCrs", "displayCrs", "viewState"},
        )
        path = require_path(params["path"], "path")
        name = require_string(params["name"], "name", maximum=MAX_NAME_LENGTH)
        description = require_string(
            params["description"], "description", maximum=MAX_DESCRIPTION_LENGTH, allow_empty=True
        )
        analysis_crs = require_crs(params["analysisCrs"], "analysisCrs", nullable=True)
        display_crs = require_crs(params["displayCrs"], "displayCrs")
        view_state = require_view_state(params["viewState"])
        if self._session is None or not _same_path(path, self._session.path):
            raise DomainError("Save path is not the active project", kind="project_not_active", detail=str(path))

        existing = _read_project(path)
        updated_at = _utc_now(existing["updatedAt"])
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(path, timeout=10.0)
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    "UPDATE project_metadata SET name = ?, description = ?, updated_at = ?, "
                    "analysis_crs = ?, display_crs = ?, view_state = ? WHERE singleton = 1 AND project_id = ?",
                    (
                        name,
                        description,
                        updated_at,
                        analysis_crs,
                        display_crs,
                        json.dumps(view_state, separators=(",", ":")),
                        existing["id"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise sqlite3.DatabaseError("project metadata row is missing")
        except sqlite3.DatabaseError as exc:
            raise DomainError("Could not save project", kind="project_save_failed", detail=str(exc)) from exc
        finally:
            if connection is not None:
                connection.close()
        return _read_project(path)

    def close(self) -> dict[str, bool]:
        if self._session:
            self._session.close()
            self._session = None
        return {"closed": True}

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _replace_session(self, session: ProjectSession) -> None:
        previous = self._session
        self._session = session
        if previous:
            previous.close()
