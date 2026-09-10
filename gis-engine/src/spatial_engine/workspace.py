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


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail=f"{label} is invalid")
    return float(value)


def _string_map(value: Any, label: str, *, maximum: int = 1024, item_maximum: int = 8192) -> None:
    if not isinstance(value, dict) or len(value) > maximum:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail=f"{label} is invalid")
    for key, item in value.items():
        if not isinstance(key, str) or len(key) > 256 or not isinstance(item, str) or len(item) > item_maximum:
            raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail=f"{label} is invalid")


def _validate_raster_info(value: Any) -> None:
    expected = {
        "width", "height", "bandCount", "transform", "resolution", "bands", "horizontalUnit",
        "verticalCrsWkt", "tags", "tagNamespaces",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="raster metadata is invalid")
    width = _nonnegative_int(value["width"], "raster width")
    height = _nonnegative_int(value["height"], "raster height")
    band_count = _nonnegative_int(value["bandCount"], "raster band count")
    if not 1 <= width <= 100_000 or not 1 <= height <= 100_000 or not 1 <= band_count <= 16:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="raster dimensions are invalid")
    transform = value["transform"]
    if not isinstance(transform, list) or len(transform) != 6:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="raster transform is invalid")
    transform = [_finite_number(item, "raster transform") for item in transform]
    if transform[0] * transform[4] - transform[1] * transform[3] == 0:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="raster transform is invalid")
    resolution = value["resolution"]
    if not isinstance(resolution, list) or len(resolution) != 2:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="raster resolution is invalid")
    if any(_finite_number(item, "raster resolution") <= 0 for item in resolution):
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="raster resolution is invalid")
    bands = value["bands"]
    if not isinstance(bands, list) or len(bands) != band_count:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="raster bands are invalid")
    band_keys = {
        "index", "dtype", "description", "unit", "scale", "offset", "noData", "colorInterpretation",
        "maskFlags", "overviews", "tags", "sampleMin", "sampleMax", "sampledPixels", "validSamplePixels",
    }
    for expected_index, band in enumerate(bands, 1):
        if not isinstance(band, dict) or set(band) != band_keys or band["index"] != expected_index:
            raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="raster band is invalid")
        for key in ("dtype", "colorInterpretation"):
            _text(band[key], f"raster band {key}", 256)
        for key in ("description", "unit"):
            _text(band[key], f"raster band {key}", 8192, nullable=True)
        _finite_number(band["scale"], "raster band scale")
        _finite_number(band["offset"], "raster band offset")
        no_data = band["noData"]
        if no_data is not None and no_data not in ("NaN", "Infinity", "-Infinity"):
            _finite_number(no_data, "raster band NoData")
        flags = band["maskFlags"]
        if not isinstance(flags, list) or len(flags) > 64 or any(not isinstance(item, str) or len(item) > 256 for item in flags):
            raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="raster mask flags are invalid")
        overviews = band["overviews"]
        if (
            not isinstance(overviews, list)
            or len(overviews) > 64
            or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in overviews)
        ):
            raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="raster overviews are invalid")
        _string_map(band["tags"], "raster band tags", item_maximum=MAX_DATASET_JSON_BYTES)
        sampled = _nonnegative_int(band["sampledPixels"], "sampled pixels")
        valid = _nonnegative_int(band["validSamplePixels"], "valid sampled pixels")
        if sampled > 256 * 256 or valid > sampled:
            raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="raster sample counts are invalid")
        sample_min, sample_max = band["sampleMin"], band["sampleMax"]
        if valid == 0:
            if sample_min is not None or sample_max is not None:
                raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="raster sample range is invalid")
        else:
            low = _finite_number(sample_min, "raster sample minimum")
            high = _finite_number(sample_max, "raster sample maximum")
            if low > high:
                raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="raster sample range is invalid")
    for key in ("horizontalUnit", "verticalCrsWkt"):
        _text(value[key], f"raster {key}", 131_072, nullable=True)
    _string_map(value["tags"], "raster tags", item_maximum=MAX_DATASET_JSON_BYTES)
    namespaces = value["tagNamespaces"]
    if not isinstance(namespaces, dict) or len(namespaces) > 256:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="raster tag namespaces are invalid")
    for namespace, tags in namespaces.items():
        if not isinstance(namespace, str) or not namespace or len(namespace) > 256:
            raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="raster tag namespace is invalid")
        _string_map(tags, "raster namespace tags", item_maximum=MAX_DATASET_JSON_BYTES)


def _validate_dataset(dataset: Any) -> tuple[dict[str, Any], str]:
    if not isinstance(dataset, dict):
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset")
    common = {
        "id", "version", "name", "kind", "source", "relativePath", "crsWkt", "crsAuthority", "bounds",
        "boundsWgs84", "report", "createdAt",
    }
    kind = dataset.get("kind")
    if kind == "raster":
        expected = common | {"raster"}
    else:
        expected = common | {
            "storageLayer", "featureCount", "geometryType", "fields", "internalIdField", "sourceFidField",
        }
        if kind == "table":
            expected.add("cellMetadataLayer")
    if set(dataset) != expected:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="dataset fields do not match")
    dataset_id = _uuid_string(dataset["id"], "dataset id")
    if not isinstance(dataset["version"], str) or re.fullmatch(r"[0-9a-fA-F]{64}", dataset["version"]) is None:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="dataset version is invalid")
    _text(dataset["name"], "dataset name", MAX_NAME_LENGTH)
    if kind not in {"vector", "table", "raster"}:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="kind is invalid")
    source = dataset["source"]
    source_keys = {"path", "layer", "driver", "fingerprint", "encoding", "assignedCrs", "crsWkt", "metadata"}
    if not isinstance(source, dict) or set(source) != source_keys:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="source fields do not match")
    for key in ("path", "driver", "fingerprint"):
        _text(source[key], f"source {key}", 32_767)
    if kind == "raster":
        if not isinstance(source["layer"], str) or len(source["layer"]) > 32_767:
            raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="source layer is invalid")
    else:
        _text(source["layer"], "source layer", 32_767)
    for key in ("encoding", "assignedCrs", "crsWkt"):
        _text(source[key], f"source {key}", 131_072, nullable=True)
    _string_map(source["metadata"], "source metadata", maximum=256)
    expected_relative = f"rasters/{dataset_id}.tif" if kind == "raster" else f"datasets/{dataset_id}.gpkg"
    if dataset["relativePath"] != expected_relative:
        raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="relativePath is invalid")
    _text(dataset["crsWkt"], "CRS WKT", 131_072, nullable=True)
    _text(dataset["crsAuthority"], "CRS authority", 256, nullable=True)
    _bounds(dataset["bounds"], "bounds")
    _bounds(dataset["boundsWgs84"], "WGS84 bounds")
    if kind == "raster":
        _validate_raster_info(dataset["raster"])
    else:
        storage_layer = _text(dataset["storageLayer"], "storage layer", 256)
        if kind == "table" and storage_layer != "records":
            raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="table storage layer is invalid")
        _nonnegative_int(dataset["featureCount"], "feature count")
    if kind == "vector":
        _text(dataset["geometryType"], "geometry type", 256)
    elif kind == "table":
        if any(dataset[key] is not None for key in ("geometryType", "crsWkt", "crsAuthority", "bounds", "boundsWgs84")):
            raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="table spatial metadata must be null")
        if source["assignedCrs"] is not None or source["crsWkt"] is not None:
            raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="table source CRS must be null")
        if dataset["cellMetadataLayer"] not in {None, "cell_metadata"}:
            raise DomainError("Dataset metadata is invalid", kind="invalid_dataset", detail="cell metadata layer is invalid")
    if kind != "raster":
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
    from .recovery import recovery_hash_read
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := recovery_hash_read(handle):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


class WorkspaceStore:
    def __init__(self, projects: ProjectStore) -> None:
        self.projects = projects
        self._recovered_session: ProjectSession | None = None
        self._recovery = None

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
            dataset_rows = connection.execute(
                "SELECT dataset_id, dataset_json FROM datasets ORDER BY created_at, dataset_id"
            ).fetchall()
            datasets = [self._stored_dataset(row["dataset_json"], row["dataset_id"]) for row in dataset_rows]
            datasets_by_id = {dataset["id"]: dataset for dataset in datasets}
            layers = [self._row_to_layer(row, datasets_by_id.get(row["dataset_id"])) for row in connection.execute(
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
            row = connection.execute("SELECT dataset_json FROM datasets WHERE dataset_id = ?", (dataset_id,)).fetchone()
        if row is None:
            raise DomainError("Dataset does not exist", kind="dataset_not_found", detail=dataset_id)
        return self._stored_dataset(row[0], dataset_id)

    def source_status(self, params: dict[str, Any]) -> dict[str, Any]:
        from .portability import source_status
        return source_status(self, params)

    def managed_path(self, dataset: dict[str, Any]) -> Path:
        dataset, _ = _validate_dataset(dataset)
        root = self._project_root().resolve()
        directory = "rasters" if dataset["kind"] == "raster" else "datasets"
        filename = f"{dataset['id']}.tif" if dataset["kind"] == "raster" else f"{dataset['id']}.gpkg"
        dataset_root = (root / directory).resolve(strict=False)
        try:
            dataset_root.relative_to(root)
        except ValueError as exc:
            raise DomainError("Managed dataset directory escapes the project", kind="invalid_project") from exc
        relative = PurePosixPath(dataset["relativePath"])
        if relative.is_absolute() or ".." in relative.parts or relative.parts != (directory, filename):
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
        allowed = {"name", "visible", "opacity", "color", "categoryField", "categoryColors", "rasterStyle"}
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
            dataset_row = connection.execute(
                "SELECT dataset_json FROM datasets WHERE dataset_id = ?", (row["dataset_id"],)
            ).fetchone()
            if dataset_row is None:
                raise DomainError("Layer references a missing dataset", kind="invalid_project", detail=layer_id)
            dataset = self._stored_dataset(dataset_row[0], row["dataset_id"])
            vector_changes = {"color", "categoryField", "categoryColors"} & set(changes)
            raster_changes = {"rasterStyle"} & set(changes)
            if dataset["kind"] == "vector":
                if raster_changes:
                    raise InvalidParamsError("rasterStyle is only supported for raster layers")
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
            elif dataset["kind"] == "raster":
                if vector_changes:
                    raise InvalidParamsError("Vector style properties are only supported for vector layers")
                if "rasterStyle" in changes:
                    from . import rasters

                    values["raster_style"] = _json_dumps(rasters.validate_style(changes["rasterStyle"], dataset))
            else:
                raise DomainError("Table datasets cannot have map layers", kind="invalid_project", detail=layer_id)
            assignments = ", ".join(f"{column} = ?" for column in values)
            connection.execute(
                f"UPDATE map_layers SET {assignments} WHERE layer_id = ?", (*values.values(), layer_id)
            )
            updated = connection.execute("SELECT * FROM map_layers WHERE layer_id = ?", (layer_id,)).fetchone()
        return self._row_to_layer(updated, dataset)

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
            rows = connection.execute(
                "SELECT map_layers.*, datasets.dataset_json FROM map_layers "
                "JOIN datasets ON datasets.dataset_id = map_layers.dataset_id "
                "ORDER BY display_order, layer_id"
            ).fetchall()
        return [self._row_to_layer(row, self._stored_dataset(row["dataset_json"], row["dataset_id"])) for row in rows]

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
        if kind not in {"import", "export", "points", "save_as", "relocate", "analysis"}:
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

    def prepare_import_publication(self, task_id: str, dataset: dict[str, Any], staged_path: Path, *, proof=None) -> None:
        dataset, serialized = _validate_dataset(dataset)
        task = self.task(task_id)
        if task["kind"] not in {"import", "points", "analysis"} or task["status"] != "running" or task["datasetId"] != dataset["id"]:
            raise DomainError("Import task does not match its result", kind="invalid_worker_result")
        if task["kind"] == "points" and dataset["kind"] != "vector":
            raise DomainError("Point task did not produce a vector dataset", kind="invalid_worker_result")
        if task["kind"] == "analysis" and (dataset["kind"] != "vector" or dataset["source"]["driver"] != "SpatialAnalysis"):
            raise DomainError("Analysis task did not produce an analysis result", kind="invalid_worker_result")
        root = self._project_root().resolve()
        staged = staged_path.resolve(strict=False)
        expected_parent = (root / "staging" / "tasks" / task_id).resolve(strict=False)
        expected_name = "snapshot.tif" if dataset["kind"] == "raster" else "snapshot.gpkg"
        if staged.parent != expected_parent or staged.name != expected_name or not staged.is_file():
            raise DomainError("Worker artifact path is invalid", kind="invalid_worker_result")
        from .publication import check_proof
        size, digest = _sha256(staged) if proof is None else check_proof(staged, proof)
        if dataset["version"].lower() != digest:
            raise DomainError("Dataset version does not match its artifact", kind="invalid_worker_result")
        staged_relative = staged.relative_to(root).as_posix()
        path = self.projects.active_path(str(self.projects._session.path))
        with self._connect(path) as connection:
            existing_count = connection.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
            if existing_count >= MAX_DATASETS:
                raise DomainError("Project dataset limit reached", kind="dataset_limit")
            connection.execute(
                "INSERT INTO pending_publications VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, serialized, staged_relative, dataset["relativePath"], size, digest),
            )

    def validate_import_journal(self, pending):
        from .capacity import MAX_SNAPSHOT_BYTES
        task_id = pending["task_id"]
        try:
            if str(uuid.UUID(task_id)) != task_id:
                raise ValueError("noncanonical task ID")
            dataset, serialized = _validate_dataset(json.loads(pending["dataset_json"]))
            task = self.task(task_id)
            if (task["kind"] not in {"import", "points", "analysis"}
                    or task["status"] not in {"running", "interrupted"}
                    or task["datasetId"] != dataset["id"]):
                raise ValueError("task binding mismatch")
            if task["kind"] == "points" and dataset["kind"] != "vector":
                raise ValueError("point result kind mismatch")
            if task["kind"] == "analysis" and (dataset["kind"] != "vector" or dataset["source"]["driver"] != "SpatialAnalysis"):
                raise ValueError("analysis result kind mismatch")
            name = "snapshot.tif" if dataset["kind"] == "raster" else "snapshot.gpkg"
            if (pending["staged_relative_path"] != f"staging/tasks/{task_id}/{name}"
                    or pending["final_relative_path"] != dataset["relativePath"]
                    or pending["artifact_sha256"] != dataset["version"]
                    or type(pending["artifact_size"]) is not int
                    or not 0 < pending["artifact_size"] <= MAX_SNAPSHOT_BYTES):
                raise ValueError("artifact binding mismatch")
            root = self._project_root().resolve()
            staged = self._safe_project_relative(root, pending["staged_relative_path"], "staged artifact")
            final = self._safe_project_relative(root, pending["final_relative_path"], "dataset artifact")
            if staged.parent != root / "staging" / "tasks" / task_id:
                raise ValueError("staged artifact escapes task directory")
            return dataset, serialized, staged, final
        except (ValueError, TypeError, KeyError) as exc:
            raise DomainError("Import publication journal is invalid", kind="invalid_project", detail=str(exc)) from exc

    def complete_import_publication(self, task_id: str, *, recovering: bool = False, proof=None) -> dict[str, Any]:
        path = self.projects.active_path(str(self.projects._session.path) if self.projects._session else "")
        with self._connect(path) as connection:
            pending = connection.execute(
                "SELECT * FROM pending_publications WHERE task_id = ?", (task_id,)
            ).fetchone()
        if pending is None:
            raise DomainError("Import publication is missing", kind="invalid_worker_result")
        dataset, serialized, staged, final = self.validate_import_journal(pending)
        expected_size = pending["artifact_size"]
        expected_digest = pending["artifact_sha256"]
        if final.exists():
            from .publication import check_proof
            if not recovering or not final.is_file() or (_sha256(final) if proof is None else check_proof(final, proof)) != (expected_size, expected_digest):
                raise DomainError("Dataset destination already exists", kind="publication_conflict", detail=str(final))
        else:
            from .publication import check_proof
            if not staged.is_file() or (_sha256(staged) if proof is None else check_proof(staged, proof)) != (expected_size, expected_digest):
                raise DomainError("Staged dataset artifact is missing or changed", kind="invalid_worker_result")
            final.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._rename_no_replace(staged, final)
            except FileExistsError as exc:
                raise DomainError("Dataset destination already exists", kind="publication_conflict", detail=str(final)) from exc
            except OSError as exc:
                raise DomainError("Could not publish dataset", kind="publication_failed", detail=str(exc)) from exc
        if proof is not None:
            proof.lease.require_identity(final)
        timestamp = _utc_now()
        with self._connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = connection.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
            if count >= MAX_DATASETS:
                raise DomainError("Project dataset limit reached", kind="dataset_limit")
            connection.execute(
                "INSERT INTO datasets (dataset_id, version, relative_path, dataset_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (dataset["id"], dataset["version"], dataset["relativePath"], serialized, dataset["createdAt"]),
            )
            if dataset["kind"] in {"vector", "raster"}:
                next_order = connection.execute("SELECT COALESCE(MAX(display_order), -1) + 1 FROM map_layers").fetchone()[0]
                raster_style = None
                if dataset["kind"] == "raster":
                    from . import rasters

                    raster_style = _json_dumps(rasters.validate_style(rasters.default_style(dataset), dataset))
                connection.execute(
                    "INSERT INTO map_layers "
                    "(layer_id, dataset_id, name, visible, opacity, color, category_field, category_colors, display_order, raster_style) "
                    "VALUES (?, ?, ?, 1, 1.0, '#2563EB', NULL, '{}', ?, ?)",
                    (str(uuid.uuid4()), dataset["id"], dataset["name"], next_order, raster_style),
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
                "UNION ALL SELECT 1 FROM pending_exports WHERE task_id = ? "
                "UNION ALL SELECT 1 FROM pending_copies WHERE task_id = ? LIMIT 1", (task_id, task_id, task_id)
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

    def complete_export_publication(self, task_id: str, *, recovering: bool = False, proof=None) -> dict[str, Any]:
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
        dataset = self.dataset(str(path), task["datasetId"]) if task["datasetId"] is not None else None
        if dataset is not None and dataset["kind"] == "raster":
            from . import rasters

            rasters._sidecars(destination)
        expected = (pending["artifact_size"], pending["artifact_sha256"])
        if destination.exists():
            from .publication import check_proof
            if not recovering or not destination.is_file() or (_sha256(destination) if proof is None else check_proof(destination, proof)) != expected:
                raise DomainError("Export destination already exists", kind="destination_exists", detail=str(destination))
        else:
            if not temporary.is_file() or temporary.stat().st_size != expected[0]:
                raise DomainError("Pending export is missing or changed", kind="invalid_worker_result")
            from .publication import check_proof
            if (recovering and proof is None and _sha256(temporary) != expected) or (proof is not None and check_proof(temporary, proof) != expected):
                raise DomainError("Pending export is missing or changed", kind="invalid_worker_result")
            try:
                self._rename_no_replace(temporary, destination)
            except FileExistsError as exc:
                raise DomainError("Export destination already exists", kind="destination_exists", detail=str(destination)) from exc
            except OSError as exc:
                raise DomainError("Could not publish export", kind="export_failed", detail=str(exc)) from exc
        if proof is not None:
            proof.lease.require_identity(destination)
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
            if self._recovery is not None:
                self._recovery.tick()
            return
        from .recovery import RecoveryCoordinator, INLINE_RECOVERY_BYTES, INLINE_HASH_REMAINING
        coordinator = RecoveryCoordinator(self)
        if coordinator.total_bytes <= INLINE_RECOVERY_BYTES and not coordinator.errors:
            token = INLINE_HASH_REMAINING.set(INLINE_RECOVERY_BYTES)
            try:
                self._recover_inline()
            finally:
                INLINE_HASH_REMAINING.reset(token)
            return
        self.interrupt_running_tasks()
        self._cleanup_unjournaled()
        self._recovered_session = session
        self._recovery = coordinator
        self._recovery.tick()

    @property
    def recovery_active(self) -> bool:
        return self._recovery is not None and self._recovery.active

    def cancel_recovery(self, task_id: str):
        return self._recovery.cancel(task_id) if self._recovery is not None else None

    def close_recovery(self) -> None:
        if self._recovery is not None:
            self._recovery.close()
            self._recovery = None
            self._recovered_session = None

    def _recover_inline(self) -> None:
        session = self.projects._session
        if session is None:
            raise DomainError("No project is active", kind="project_not_active")
        if self._recovered_session is session:
            return
        path = session.path
        from .portability import recover_copies
        recover_copies(self)
        with self._connect(path) as connection:
            task_ids = [row[0] for row in connection.execute("SELECT task_id FROM pending_publications")]
        for task_id in task_ids:
            try:
                self.complete_import_publication(task_id, recovering=True)
                self._cleanup_task_directory(task_id)
            except DomainError as exc:
                self.update_task(task_id, status="interrupted", stage="publication_pending", error=exc.message)
        with self._connect(path) as connection:
            export_task_ids = [row[0] for row in connection.execute("SELECT task_id FROM pending_exports")]
        for task_id in export_task_ids:
            try:
                self.complete_export_publication(task_id, recovering=True)
                self._cleanup_task_directory(task_id)
            except DomainError as exc:
                self.update_task(task_id, status="interrupted", stage="publication_pending", error=exc.message)
        self._cleanup_unjournaled()
        self.interrupt_running_tasks()
        self._recovered_session = session

    def _cleanup_unjournaled(self) -> None:
        path = self.projects._session.path
        with self._connect(path) as connection:
            unjournaled = connection.execute(
                "SELECT task_id, kind, destination FROM tasks WHERE "
                "task_id NOT IN (SELECT task_id FROM pending_publications) "
                "AND task_id NOT IN (SELECT task_id FROM pending_exports) "
                "AND task_id NOT IN (SELECT task_id FROM pending_copies)"
            ).fetchall()
        for row in unjournaled:
            if row["kind"] != "export" or self._remove_owned_export_pending(row["task_id"], row["destination"]):
                self._cleanup_task_directory(row["task_id"])

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
    def _stored_dataset(serialized: Any, dataset_id: str) -> dict[str, Any]:
        try:
            dataset = json.loads(serialized)
            _validate_dataset(dataset)
        except (json.JSONDecodeError, TypeError, DomainError) as exc:
            raise DomainError("Dataset metadata is corrupt", kind="invalid_project", detail=dataset_id) from exc
        return dataset

    @staticmethod
    def _row_to_layer(row: sqlite3.Row, dataset: dict[str, Any] | None) -> dict[str, Any]:
        if dataset is None:
            raise DomainError("Layer references a missing dataset", kind="invalid_project", detail=row["layer_id"])
        try:
            category_colors = json.loads(row["category_colors"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise DomainError("Layer metadata is corrupt", kind="invalid_project", detail=row["layer_id"]) from exc
        layer = {
            "id": row["layer_id"], "datasetId": row["dataset_id"], "name": row["name"],
            "visible": bool(row["visible"]), "opacity": row["opacity"], "color": row["color"],
            "categoryField": row["category_field"], "categoryColors": category_colors, "order": row["display_order"],
        }
        if dataset["kind"] == "raster":
            if row["raster_style"] is None:
                raise DomainError("Raster layer style is missing", kind="invalid_project", detail=row["layer_id"])
            try:
                from . import rasters

                layer["rasterStyle"] = rasters.validate_style(json.loads(row["raster_style"]), dataset)
            except (json.JSONDecodeError, TypeError, InvalidParamsError) as exc:
                raise DomainError("Raster layer style is corrupt", kind="invalid_project", detail=row["layer_id"]) from exc
        elif dataset["kind"] == "vector":
            if row["raster_style"] is not None:
                raise DomainError("Vector layer has a raster style", kind="invalid_project", detail=row["layer_id"])
        else:
            raise DomainError("Table datasets cannot have map layers", kind="invalid_project", detail=row["layer_id"])
        return layer
