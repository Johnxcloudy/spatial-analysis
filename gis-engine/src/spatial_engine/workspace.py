from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from .errors import DomainError, InvalidParamsError
from .projects import ProjectSession, ProjectStore, _same_path, _utc_now
from .validation import MAX_NAME_LENGTH, require_exact_keys, require_finite_number, require_path, require_string

MAX_DATASETS = 64
MAX_DATASET_JSON_BYTES = 256 * 1024
MAX_TASK_HISTORY = 100
MAX_WORKSPACE_JSON_BYTES = 6 * 1024 * 1024
MAX_FIELDS = 256
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_TASK_STATUSES = {"running", "completed", "failed", "cancelled", "interrupted"}


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail=str(exc)) from exc


def _uuid_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail=f"{label} is invalid")
    try:
        uuid.UUID(value)
    except ValueError as exc:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail=f"{label} is invalid") from exc
    return value


def _text(value: Any, label: str, maximum: int = 8192, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail=f"{label} is invalid")
    return value


def _nonnegative_int(value: Any, label: str, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail=f"{label} is invalid")
    return value


def _bounds(value: Any, label: str) -> list[float] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 4:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail=f"{label} is invalid")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) for item in value):
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail=f"{label} is invalid")
    result = [float(item) for item in value]
    if result[0] > result[2] or result[1] > result[3]:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail=f"{label} is invalid")
    return result


def _validate_dataset(dataset: Any) -> tuple[dict[str, Any], str]:
    if not isinstance(dataset, dict):
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset")
    expected = {
        "id", "version", "name", "kind", "source", "relativePath", "storageLayer", "featureCount",
        "geometryType", "crsWkt", "crsAuthority", "bounds", "boundsWgs84", "fields", "internalIdField",
        "sourceFidField", "report", "createdAt",
    }
    if set(dataset) != expected:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="dataset fields do not match")
    dataset_id = _uuid_string(dataset["id"], "dataset id")
    if not isinstance(dataset["version"], str) or re.fullmatch(r"[0-9a-fA-F]{64}", dataset["version"]) is None:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="dataset version is invalid")
    _text(dataset["name"], "dataset name", MAX_NAME_LENGTH)
    if dataset["kind"] != "vector":
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="kind must be vector")
    source = dataset["source"]
    source_keys = {"path", "layer", "driver", "fingerprint", "encoding", "assignedCrs", "crsWkt", "metadata"}
    if not isinstance(source, dict) or set(source) != source_keys:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="source fields do not match")
    for key in ("path", "layer", "driver", "fingerprint"):
        _text(source[key], f"source {key}", 32_767)
    for key in ("encoding", "assignedCrs", "crsWkt"):
        _text(source[key], f"source {key}", 131_072, nullable=True)
    metadata = source["metadata"]
    if not isinstance(metadata, dict) or len(metadata) > 256:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="source metadata is invalid")
    for key, value in metadata.items():
        if not isinstance(key, str) or not isinstance(value, str) or len(key) > 256 or len(value) > 8192:
            raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="source metadata is invalid")
    expected_relative = f"datasets/{dataset_id}.gpkg"
    if dataset["relativePath"] != expected_relative:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="relativePath is invalid")
    _text(dataset["storageLayer"], "storage layer", 256)
    _nonnegative_int(dataset["featureCount"], "feature count")
    _text(dataset["geometryType"], "geometry type", 256)
    _text(dataset["crsWkt"], "CRS WKT", 131_072, nullable=True)
    _text(dataset["crsAuthority"], "CRS authority", 256, nullable=True)
    _bounds(dataset["bounds"], "bounds")
    _bounds(dataset["boundsWgs84"], "WGS84 bounds")
    fields = dataset["fields"]
    if not isinstance(fields, list) or len(fields) > MAX_FIELDS:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="fields are invalid")
    field_names: set[str] = set()
    field_keys = {"name", "sourceType", "storageType", "nullable", "alias", "width", "precision", "metadataStatus"}
    for field in fields:
        if not isinstance(field, dict) or set(field) != field_keys:
            raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="field metadata is invalid")
        name = _text(field["name"], "field name", 256)
        if name in field_names:
            raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="field names must be unique")
        field_names.add(name)
        _text(field["sourceType"], "source type", 256)
        _text(field["storageType"], "storage type", 256)
        if field["nullable"] is not None and not isinstance(field["nullable"], bool):
            raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="nullable is invalid")
        _text(field["alias"], "field alias", 1024, nullable=True)
        _nonnegative_int(field["width"], "field width", nullable=True)
        _nonnegative_int(field["precision"], "field precision", nullable=True)
        if field["metadataStatus"] not in {"not_read", "partial"}:
            raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="metadataStatus is invalid")
    _text(dataset["internalIdField"], "internal id field", 256)
    _text(dataset["sourceFidField"], "source FID field", 256)
    report = dataset["report"]
    if not isinstance(report, dict) or set(report) != {
        "status", "checks", "warnings", "notChecked", "counts", "validatorVersion"
    }:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="report is invalid")
    if report["status"] not in {"warning", "restricted"}:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="report status is invalid")
    if not isinstance(report["checks"], list) or len(report["checks"]) > 512:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="report checks are invalid")
    for check in report["checks"]:
        if not isinstance(check, dict) or set(check) not in ({"code", "passed", "detail"}, {"code", "passed", "detail", "count"}):
            raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="report check is invalid")
        _text(check["code"], "check code", 256)
        if not isinstance(check["passed"], bool):
            raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="check passed is invalid")
        _text(check["detail"], "check detail", 8192)
        if "count" in check:
            _nonnegative_int(check["count"], "check count")
    for key in ("warnings", "notChecked"):
        values = report[key]
        if not isinstance(values, list) or len(values) > 512 or any(not isinstance(value, str) for value in values):
            raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail=f"report {key} is invalid")
    counts = report["counts"]
    if not isinstance(counts, dict) or len(counts) > 256:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="report counts are invalid")
    for key, value in counts.items():
        if not isinstance(key, str):
            raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="report counts are invalid")
        _nonnegative_int(value, "report count")
    _text(report["validatorVersion"], "validator version", 256)
    _text(dataset["createdAt"], "createdAt", 64)
    serialized = _json_dumps(dataset)
    if len(serialized.encode("utf-8")) > MAX_DATASET_JSON_BYTES:
        raise DomainError("Dataset metadata exceeds the storage limit", kind="dataset_too_large")
    return dataset, serialized


def _row_to_task(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["task_id"], "kind": row["kind"], "status": row["status"], "stage": row["stage"],
        "completed": row["completed"], "total": row["total"], "createdAt": row["created_at"],
        "updatedAt": row["updated_at"], "datasetId": row["dataset_id"], "destination": row["destination"],
        "error": row["error"],
    }


def _sha256(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


class WorkspaceStore:
    def __init__(self, projects: ProjectStore) -> None:
        self.projects = projects
        self._recovered_session: ProjectSession | None = None

    def _path(self, path: Any) -> Path:
        return self.projects.active_path(path)

    @contextmanager
    def _connect(self, path: Path) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _project_root(self) -> Path:
        if self.projects._session is None:
            raise DomainError("No project is active", kind="project_not_active")
        return self.projects._session.path.parent

    def get(self, params: dict[str, Any]) -> dict[str, Any]:
        require_exact_keys(params, {"path"})
        path = self._path(params["path"])
        self.recover()
        with self._connect(path) as connection:
            project_id = connection.execute("SELECT project_id FROM project_metadata WHERE singleton = 1").fetchone()[0]
            datasets = [json.loads(row[0]) for row in connection.execute(
                "SELECT dataset_json FROM vector_datasets ORDER BY created_at, dataset_id"
            )]
            layers = [self._row_to_layer(row) for row in connection.execute(
                "SELECT * FROM map_layers ORDER BY display_order, layer_id"
            )]
            tasks = [_row_to_task(row) for row in connection.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC, task_id DESC LIMIT ?", (MAX_TASK_HISTORY,)
            )]
        result = {"projectId": project_id, "datasets": datasets, "layers": layers, "tasks": tasks}
        if len(_json_dumps(result).encode("utf-8")) > MAX_WORKSPACE_JSON_BYTES:
            raise DomainError("Workspace exceeds the response limit", kind="workspace_too_large")
        return result

    def dataset(self, path: Any, dataset_id: Any) -> dict[str, Any]:
        project_path = self._path(path)
        dataset_id = require_string(dataset_id, "datasetId", maximum=64)
        with self._connect(project_path) as connection:
            row = connection.execute("SELECT dataset_json FROM vector_datasets WHERE dataset_id = ?", (dataset_id,)).fetchone()
        if row is None:
            raise DomainError("Dataset does not exist", kind="dataset_not_found", detail=dataset_id)
        try:
            dataset = json.loads(row[0])
        except (json.JSONDecodeError, TypeError) as exc:
            raise DomainError("Dataset metadata is corrupt", kind="invalid_project", detail=dataset_id) from exc
        _validate_dataset(dataset)
        return dataset

    def managed_path(self, dataset: dict[str, Any]) -> Path:
        dataset, _ = _validate_dataset(dataset)
        root = self._project_root().resolve()
        dataset_root = (root / "datasets").resolve(strict=False)
        try:
            dataset_root.relative_to(root)
        except ValueError as exc:
            raise DomainError("Managed dataset directory escapes the project", kind="invalid_project") from exc
        relative = PurePosixPath(dataset["relativePath"])
        if relative.is_absolute() or ".." in relative.parts or relative.parts != ("datasets", f"{dataset['id']}.gpkg"):
            raise DomainError("Dataset path is invalid", kind="invalid_dataset")
        managed = (root / Path(*relative.parts)).resolve(strict=False)
        if managed.parent != dataset_root:
            raise DomainError("Dataset path escapes the project", kind="invalid_dataset")
        if not managed.is_file():
            raise DomainError("Managed dataset file is missing", kind="dataset_missing", detail=str(managed))
        return managed

    def update_layer(self, params: dict[str, Any]) -> dict[str, Any]:
        require_exact_keys(params, {"path", "layerId", "changes"})
        path = self._path(params["path"])
        layer_id = require_string(params["layerId"], "layerId", maximum=64)
        changes = params["changes"]
        allowed = {"name", "visible", "opacity", "color", "categoryField", "categoryColors"}
        if not isinstance(changes, dict) or not changes or not set(changes) <= allowed:
            raise InvalidParamsError("changes must contain supported layer properties")
        with self._connect(path) as connection:
            row = connection.execute("SELECT * FROM map_layers WHERE layer_id = ?", (layer_id,)).fetchone()
            if row is None:
                raise DomainError("Layer does not exist", kind="layer_not_found", detail=layer_id)
            values: dict[str, Any] = {}
            if "name" in changes:
                values["name"] = require_string(changes["name"], "changes.name", maximum=MAX_NAME_LENGTH)
            if "visible" in changes:
                if not isinstance(changes["visible"], bool):
                    raise InvalidParamsError("changes.visible must be a boolean")
                values["visible"] = int(changes["visible"])
            if "opacity" in changes:
                opacity = require_finite_number(changes["opacity"], "changes.opacity")
                if not 0 <= opacity <= 1:
                    raise InvalidParamsError("changes.opacity must be between 0 and 1")
                values["opacity"] = opacity
            if "color" in changes:
                values["color"] = self._color(changes["color"], "changes.color")
            dataset = json.loads(connection.execute(
                "SELECT dataset_json FROM vector_datasets WHERE dataset_id = ?", (row["dataset_id"],)
            ).fetchone()[0])
            field_names = {field["name"] for field in dataset["fields"]}
            if "categoryField" in changes:
                category_field = changes["categoryField"]
                if category_field is not None:
                    category_field = require_string(category_field, "changes.categoryField", maximum=256)
                    if category_field not in field_names:
                        raise InvalidParamsError("changes.categoryField is not a dataset field")
                values["category_field"] = category_field
            if "categoryColors" in changes:
                colors = changes["categoryColors"]
                if not isinstance(colors, dict) or len(colors) > 256:
                    raise InvalidParamsError("changes.categoryColors must be an object with at most 256 entries")
                clean_colors: dict[str, str] = {}
                for key, color in colors.items():
                    if not isinstance(key, str) or len(key) > 1024:
                        raise InvalidParamsError("category color keys must be strings")
                    clean_colors[key] = self._color(color, "category color")
                values["category_colors"] = json.dumps(clean_colors, separators=(",", ":"))
            assignments = ", ".join(f"{column} = ?" for column in values)
            connection.execute(
                f"UPDATE map_layers SET {assignments} WHERE layer_id = ?", (*values.values(), layer_id)
            )
            updated = connection.execute("SELECT * FROM map_layers WHERE layer_id = ?", (layer_id,)).fetchone()
        return self._row_to_layer(updated)

    def reorder_layers(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        require_exact_keys(params, {"path", "layerIds"})
        path = self._path(params["path"])
        layer_ids = params["layerIds"]
        if not isinstance(layer_ids, list) or any(not isinstance(item, str) for item in layer_ids):
            raise InvalidParamsError("layerIds must be an array of strings")
        if len(layer_ids) != len(set(layer_ids)):
            raise InvalidParamsError("layerIds must not contain duplicates")
        with self._connect(path) as connection:
            current = {row[0] for row in connection.execute("SELECT layer_id FROM map_layers")}
            if set(layer_ids) != current or len(layer_ids) != len(current):
                raise InvalidParamsError("layerIds must contain every current layer exactly once")
            # Avoid transient UNIQUE collisions while assigning the requested order.
            connection.execute("UPDATE map_layers SET display_order = display_order + 1000000")
            for order, layer_id in enumerate(layer_ids):
                connection.execute("UPDATE map_layers SET display_order = ? WHERE layer_id = ?", (order, layer_id))
            rows = connection.execute("SELECT * FROM map_layers ORDER BY display_order, layer_id").fetchall()
        return [self._row_to_layer(row) for row in rows]

    def remove_layer(self, params: dict[str, Any]) -> dict[str, bool]:
        require_exact_keys(params, {"path", "layerId"})
        path = self._path(params["path"])
        layer_id = require_string(params["layerId"], "layerId", maximum=64)
        with self._connect(path) as connection:
            cursor = connection.execute("DELETE FROM map_layers WHERE layer_id = ?", (layer_id,))
            if cursor.rowcount != 1:
                raise DomainError("Layer does not exist", kind="layer_not_found", detail=layer_id)
            rows = connection.execute("SELECT layer_id FROM map_layers ORDER BY display_order, layer_id").fetchall()
            connection.execute("UPDATE map_layers SET display_order = display_order + 1000000")
            for order, row in enumerate(rows):
                connection.execute("UPDATE map_layers SET display_order = ? WHERE layer_id = ?", (order, row[0]))
        return {"removed": True}

    def create_task(
        self, kind: str, *, task_id: str | None = None, dataset_id: str | None = None, destination: str | None = None
    ) -> dict[str, Any]:
        if kind not in {"import", "export"}:
            raise InvalidParamsError("task kind is invalid")
        task_id = task_id or str(uuid.uuid4())
        _uuid_string(task_id, "task id")
        if dataset_id is not None:
            _uuid_string(dataset_id, "dataset id")
        timestamp = _utc_now()
        path = self.projects.active_path(str(self.projects._session.path) if self.projects._session else "")
        with self._connect(path) as connection:
            connection.execute(
                "INSERT INTO tasks VALUES (?, ?, 'running', 'queued', NULL, NULL, ?, ?, ?, ?, NULL)",
                (task_id, kind, timestamp, timestamp, dataset_id, destination),
            )
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return _row_to_task(row)

    def task(self, task_id: str) -> dict[str, Any]:
        task_id = require_string(task_id, "taskId", maximum=64)
        path = self.projects.active_path(str(self.projects._session.path) if self.projects._session else "")
        with self._connect(path) as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise DomainError("Task does not exist", kind="task_not_found", detail=task_id)
        return _row_to_task(row)

    def running_task(self) -> dict[str, Any] | None:
        path = self.projects.active_path(str(self.projects._session.path) if self.projects._session else "")
        with self._connect(path) as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE status = 'running' ORDER BY created_at LIMIT 1"
            ).fetchone()
        return _row_to_task(row) if row is not None else None

    def update_task(self, task_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {"status", "stage", "completed", "total", "error"}
        if not changes or not set(changes) <= allowed:
            raise ValueError("unsupported task update")
        if "status" in changes and changes["status"] not in _TASK_STATUSES:
            raise ValueError("invalid task status")
        if "stage" in changes and (not isinstance(changes["stage"], str) or len(changes["stage"]) > 200):
            raise ValueError("invalid task stage")
        for key in ("completed", "total"):
            if key in changes and changes[key] is not None:
                _nonnegative_int(changes[key], key)
        if "error" in changes and changes["error"] is not None:
            if not isinstance(changes["error"], str):
                raise ValueError("invalid task error")
            changes["error"] = changes["error"][:8192]
        current = self.task(task_id)
        updated_at = _utc_now(current["updatedAt"])
        path = self.projects.active_path(str(self.projects._session.path))
        columns = {"status": "status", "stage": "stage", "completed": "completed", "total": "total", "error": "error"}
        assignments = [f"{columns[key]} = ?" for key in changes] + ["updated_at = ?"]
        with self._connect(path) as connection:
            connection.execute(
                f"UPDATE tasks SET {', '.join(assignments)} WHERE task_id = ?",
                (*changes.values(), updated_at, task_id),
            )
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return _row_to_task(row)

    def prepare_import_publication(self, task_id: str, dataset: dict[str, Any], staged_path: Path) -> None:
        dataset, serialized = _validate_dataset(dataset)
        task = self.task(task_id)
        if task["kind"] != "import" or task["status"] != "running" or task["datasetId"] != dataset["id"]:
            raise DomainError("Import task does not match its result", kind="invalid_worker_result")
        root = self._project_root().resolve()
        staged = staged_path.resolve(strict=False)
        expected_parent = (root / "staging" / "tasks" / task_id).resolve(strict=False)
        if staged.parent != expected_parent or not staged.is_file():
            raise DomainError("Worker artifact path is invalid", kind="invalid_worker_result")
        size, digest = _sha256(staged)
        if dataset["version"].lower() != digest:
            raise DomainError("Dataset version does not match its artifact", kind="invalid_worker_result")
        staged_relative = staged.relative_to(root).as_posix()
        path = self.projects.active_path(str(self.projects._session.path))
        with self._connect(path) as connection:
            existing_count = connection.execute("SELECT COUNT(*) FROM vector_datasets").fetchone()[0]
            if existing_count >= MAX_DATASETS:
                raise DomainError("Project dataset limit reached", kind="dataset_limit")
            connection.execute(
                "INSERT INTO pending_publications VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, serialized, staged_relative, dataset["relativePath"], size, digest),
            )

    def complete_import_publication(self, task_id: str, *, recovering: bool = False) -> dict[str, Any]:
        path = self.projects.active_path(str(self.projects._session.path) if self.projects._session else "")
        with self._connect(path) as connection:
            pending = connection.execute(
                "SELECT * FROM pending_publications WHERE task_id = ?", (task_id,)
            ).fetchone()
        if pending is None:
            raise DomainError("Import publication is missing", kind="invalid_worker_result")
        dataset = json.loads(pending["dataset_json"])
        dataset, serialized = _validate_dataset(dataset)
        root = self._project_root().resolve()
        staged = self._safe_project_relative(root, pending["staged_relative_path"], "staged artifact")
        final = self._safe_project_relative(root, pending["final_relative_path"], "dataset artifact")
        expected_size = pending["artifact_size"]
        expected_digest = pending["artifact_sha256"]
        if final.exists():
            if not recovering or not final.is_file() or _sha256(final) != (expected_size, expected_digest):
                raise DomainError("Dataset destination already exists", kind="publication_conflict", detail=str(final))
        else:
            if not staged.is_file() or _sha256(staged) != (expected_size, expected_digest):
                raise DomainError("Staged dataset artifact is missing or changed", kind="invalid_worker_result")
            final.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._rename_no_replace(staged, final)
            except FileExistsError as exc:
                raise DomainError("Dataset destination already exists", kind="publication_conflict", detail=str(final)) from exc
            except OSError as exc:
                raise DomainError("Could not publish dataset", kind="publication_failed", detail=str(exc)) from exc
        timestamp = _utc_now()
        with self._connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = connection.execute("SELECT COUNT(*) FROM vector_datasets").fetchone()[0]
            if count >= MAX_DATASETS:
                raise DomainError("Project dataset limit reached", kind="dataset_limit")
            connection.execute(
                "INSERT INTO vector_datasets VALUES (?, ?, ?, ?, ?)",
                (dataset["id"], dataset["version"], dataset["relativePath"], serialized, dataset["createdAt"]),
            )
            next_order = connection.execute("SELECT COALESCE(MAX(display_order), -1) + 1 FROM map_layers").fetchone()[0]
            connection.execute(
                "INSERT INTO map_layers VALUES (?, ?, ?, 1, 1.0, '#2563EB', NULL, '{}', ?)",
                (str(uuid.uuid4()), dataset["id"], dataset["name"], next_order),
            )
            connection.execute(
                "UPDATE tasks SET status = 'completed', stage = 'completed', completed = total, "
                "updated_at = ?, error = NULL WHERE task_id = ?", (timestamp, task_id)
            )
            connection.execute("DELETE FROM pending_publications WHERE task_id = ?", (task_id,))
        return self.task(task_id)

    def discard_pending(self, task_id: str) -> None:
        path = self.projects.active_path(str(self.projects._session.path) if self.projects._session else "")
        with self._connect(path) as connection:
            connection.execute("DELETE FROM pending_publications WHERE task_id = ?", (task_id,))
            connection.execute("DELETE FROM pending_exports WHERE task_id = ?", (task_id,))

    def has_pending(self, task_id: str) -> bool:
        path = self.projects.active_path(str(self.projects._session.path) if self.projects._session else "")
        with self._connect(path) as connection:
            row = connection.execute(
                "SELECT 1 FROM pending_publications WHERE task_id = ? "
                "UNION ALL SELECT 1 FROM pending_exports WHERE task_id = ? LIMIT 1", (task_id, task_id)
            ).fetchone()
        return row is not None

    def prepare_export_publication(
        self,
        task_id: str,
        temporary_path: Path,
        artifact_size: int | None = None,
        artifact_sha256: str | None = None,
    ) -> None:
        task = self.task(task_id)
        if task["kind"] != "export" or task["status"] != "running" or task["destination"] is None:
            raise DomainError("Export task does not match its result", kind="invalid_worker_result")
        destination = Path(task["destination"]).resolve(strict=False)
        temporary = temporary_path.resolve(strict=False)
        if temporary.parent != destination.parent or temporary.name != f".{destination.name}.{task_id}.pending":
            raise DomainError("Export publication path is invalid", kind="invalid_worker_result")
        if not temporary.is_file():
            raise DomainError("Export publication artifact is missing", kind="invalid_worker_result")
        if artifact_size is None or artifact_sha256 is None:
            artifact_size, artifact_sha256 = _sha256(temporary)
        if (
            isinstance(artifact_size, bool)
            or not isinstance(artifact_size, int)
            or artifact_size < 0
            or temporary.stat().st_size != artifact_size
            or not isinstance(artifact_sha256, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", artifact_sha256) is None
        ):
            raise DomainError("Export publication metadata is invalid", kind="invalid_worker_result")
        path = self.projects.active_path(str(self.projects._session.path) if self.projects._session else "")
        with self._connect(path) as connection:
            connection.execute(
                "INSERT INTO pending_exports VALUES (?, ?, ?, ?, ?)",
                (task_id, str(temporary), str(destination), artifact_size, artifact_sha256.lower()),
            )

    def complete_export_publication(self, task_id: str, *, recovering: bool = False) -> dict[str, Any]:
        path = self.projects.active_path(str(self.projects._session.path) if self.projects._session else "")
        with self._connect(path) as connection:
            pending = connection.execute("SELECT * FROM pending_exports WHERE task_id = ?", (task_id,)).fetchone()
        if pending is None:
            raise DomainError("Export publication is missing", kind="invalid_worker_result")
        task = self.task(task_id)
        if task["kind"] != "export" or task["destination"] != pending["destination_path"]:
            raise DomainError("Export publication journal is invalid", kind="invalid_project")
        destination = Path(pending["destination_path"]).resolve(strict=False)
        temporary = Path(pending["temporary_path"]).resolve(strict=False)
        if temporary.parent != destination.parent or temporary.name != f".{destination.name}.{task_id}.pending":
            raise DomainError("Export publication journal is invalid", kind="invalid_project")
        expected = (pending["artifact_size"], pending["artifact_sha256"])
        if destination.exists():
            if not recovering or not destination.is_file() or _sha256(destination) != expected:
                raise DomainError("Export destination already exists", kind="destination_exists", detail=str(destination))
        else:
            if not temporary.is_file() or temporary.stat().st_size != expected[0]:
                raise DomainError("Pending export is missing or changed", kind="invalid_worker_result")
            if recovering and _sha256(temporary) != expected:
                raise DomainError("Pending export is missing or changed", kind="invalid_worker_result")
            try:
                self._rename_no_replace(temporary, destination)
            except FileExistsError as exc:
                raise DomainError("Export destination already exists", kind="destination_exists", detail=str(destination)) from exc
            except OSError as exc:
                raise DomainError("Could not publish export", kind="export_failed", detail=str(exc)) from exc
        timestamp = _utc_now()
        with self._connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE tasks SET status = 'completed', stage = 'completed', updated_at = ?, error = NULL "
                "WHERE task_id = ?", (timestamp, task_id)
            )
            connection.execute("DELETE FROM pending_exports WHERE task_id = ?", (task_id,))
        return self.task(task_id)

    def recover(self) -> None:
        session = self.projects._session
        if session is None:
            raise DomainError("No project is active", kind="project_not_active")
        if self._recovered_session is session:
            return
        path = session.path
        with self._connect(path) as connection:
            task_ids = [row[0] for row in connection.execute("SELECT task_id FROM pending_publications")]
        for task_id in task_ids:
            try:
                self.complete_import_publication(task_id, recovering=True)
                self._cleanup_task_directory(task_id)
            except DomainError as exc:
                if exc.kind == "publication_failed":
                    self.update_task(
                        task_id, status="interrupted", stage="publication_pending", error=exc.message
                    )
                else:
                    self.discard_pending(task_id)
                    self.update_task(task_id, status="interrupted", stage="interrupted", error=exc.message)
                    self._cleanup_task_directory(task_id)
        with self._connect(path) as connection:
            export_task_ids = [row[0] for row in connection.execute("SELECT task_id FROM pending_exports")]
        for task_id in export_task_ids:
            try:
                self.complete_export_publication(task_id, recovering=True)
                self._cleanup_task_directory(task_id)
            except DomainError as exc:
                if exc.kind in {"export_failed", "publication_failed"}:
                    self.update_task(
                        task_id, status="interrupted", stage="publication_pending", error=exc.message
                    )
                else:
                    self._remove_pending_export_from_journal(task_id)
                    self.discard_pending(task_id)
                    self.update_task(task_id, status="interrupted", stage="interrupted", error=exc.message)
                    self._cleanup_task_directory(task_id)
        with self._connect(path) as connection:
            unjournaled = connection.execute(
                "SELECT task_id, kind, destination FROM tasks WHERE "
                "task_id NOT IN (SELECT task_id FROM pending_publications) "
                "AND task_id NOT IN (SELECT task_id FROM pending_exports)"
            ).fetchall()
        for row in unjournaled:
            if row["kind"] != "export" or self._remove_owned_export_pending(row["task_id"], row["destination"]):
                self._cleanup_task_directory(row["task_id"])
        self.interrupt_running_tasks()
        self._recovered_session = session

    def interrupt_running_tasks(self) -> None:
        session = self.projects._session
        if session is None:
            return
        timestamp = _utc_now()
        with self._connect(session.path) as connection:
            connection.execute(
                "UPDATE tasks SET status = 'interrupted', stage = 'interrupted', updated_at = ?, "
                "error = COALESCE(error, 'Task was interrupted before completion') WHERE status = 'running'",
                (timestamp,),
            )

    @staticmethod
    def _safe_project_relative(root: Path, value: Any, label: str) -> Path:
        if not isinstance(value, str):
            raise DomainError(f"{label} path is invalid", kind="invalid_project")
        relative = PurePosixPath(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise DomainError(f"{label} path is invalid", kind="invalid_project")
        path = (root / Path(*relative.parts)).resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise DomainError(f"{label} path escapes the project", kind="invalid_project") from exc
        return path

    @staticmethod
    def _rename_no_replace(source: Path, destination: Path) -> None:
        if os.name == "nt":
            os.rename(source, destination)
            return
        os.link(source, destination)
        source.unlink()

    def _cleanup_task_directory(self, task_id: str) -> None:
        try:
            uuid.UUID(task_id)
        except (ValueError, TypeError):
            return
        root = (self._project_root() / "staging" / "tasks").resolve(strict=False)
        candidate = (root / task_id).resolve(strict=False)
        if candidate.parent == root:
            shutil.rmtree(candidate, ignore_errors=True)

    def _remove_owned_export_pending(self, task_id: str, destination_value: Any) -> bool:
        if not isinstance(destination_value, str):
            return False
        destination = Path(destination_value).resolve(strict=False)
        pending = destination.with_name(f".{destination.name}.{task_id}.pending")
        marker = self._project_root() / "staging" / "tasks" / task_id / "publication-owned.json"
        try:
            raw = marker.read_bytes()
            if len(raw) > MAX_DATASET_JSON_BYTES:
                return False
            value = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            return not pending.exists()
        if value != {"path": str(pending)}:
            return False
        try:
            if pending.is_file():
                pending.unlink()
            return True
        except OSError:
            return False

    def _remove_pending_export_from_journal(self, task_id: str) -> None:
        path = self.projects.active_path(str(self.projects._session.path) if self.projects._session else "")
        with self._connect(path) as connection:
            row = connection.execute(
                "SELECT temporary_path, destination_path FROM pending_exports WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            return
        destination = Path(row["destination_path"]).resolve(strict=False)
        expected = destination.with_name(f".{destination.name}.{task_id}.pending")
        temporary = Path(row["temporary_path"]).resolve(strict=False)
        if temporary != expected:
            return
        try:
            if temporary.is_file():
                temporary.unlink()
        except OSError:
            pass

    @staticmethod
    def _color(value: Any, label: str) -> str:
        if not isinstance(value, str) or _HEX_COLOR.fullmatch(value) is None:
            raise InvalidParamsError(f"{label} must be a #RRGGBB color")
        return value.upper()

    @staticmethod
    def _row_to_layer(row: sqlite3.Row) -> dict[str, Any]:
        try:
            category_colors = json.loads(row["category_colors"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise DomainError("Layer metadata is corrupt", kind="invalid_project", detail=row["layer_id"]) from exc
        return {
            "id": row["layer_id"], "datasetId": row["dataset_id"], "name": row["name"],
            "visible": bool(row["visible"]), "opacity": row["opacity"], "color": row["color"],
            "categoryField": row["category_field"], "categoryColors": category_colors, "order": row["display_order"],
        }
