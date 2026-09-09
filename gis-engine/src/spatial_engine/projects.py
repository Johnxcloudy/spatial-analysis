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
SCHEMA_VERSION = 1
PROJECT_IDENTITY = "spatial-analysis-desktop-project"
PROJECT_FILENAME = "project.spa"
OWNED_DIRECTORIES = ("datasets", "rasters", "results", "staging", "cache")


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


def _row_to_project(row: sqlite3.Row, path: Path) -> dict[str, Any]:
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
        "schemaVersion": SCHEMA_VERSION,
        "projectPath": str(path.resolve()),
        "analysisCrs": row["analysis_crs"],
        "displayCrs": row["display_crs"],
        "viewState": view_state,
    }


def _read_project(path: Path) -> dict[str, Any]:
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
        if application_id != APPLICATION_ID or schema_version != SCHEMA_VERSION:
            raise DomainError("File is not a Spatial Analysis project", kind="invalid_project")
        row = connection.execute(
            "SELECT identity, project_id, name, description, created_at, updated_at, "
            "analysis_crs, display_crs, view_state FROM project_metadata WHERE singleton = 1"
        ).fetchone()
        if row is None or row["identity"] != PROJECT_IDENTITY:
            raise DomainError("Project identity is invalid", kind="invalid_project")
        project = _row_to_project(row, path)
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
                with connection:
                    connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
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
            return _read_project(path)
        _read_project(path)
        session = _acquire_lock(path)
        try:
            project = _read_project(path)
        except Exception:
            session.close()
            raise
        self._replace_session(session)
        return project

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
