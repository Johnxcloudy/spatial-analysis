from __future__ import annotations

import json
import math
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pyogrio
import shapely
from pyproj import Transformer

from .errors import DomainError, InvalidParamsError
from .vectors import _decode_geometry, canonical_value
from .capacity import MAX_FEATURES, MAX_GEOMETRY_VERTICES
from .publication import ArtifactLease

MAX_QUERY_BYTES = 2 * 1024 * 1024
MAX_DISPLAY_VERTICES = 100_000
MAX_DISPLAY_FEATURES = 2_000
SQL_SECONDS = 1.5


def _integer(value, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise InvalidParamsError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA trusted_schema=OFF")
    deadline = time.monotonic() + SQL_SECONDS
    connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    return connection


def _field_names(dataset: dict) -> dict[str, dict]:
    return {field["name"]: field for field in dataset["fields"]}


def _value(value, field: dict):
    value = canonical_value(value)
    if value is None:
        return None
    if field["storageType"] == "bool":
        return bool(value)
    if isinstance(value, int) and abs(value) > 9007199254740991:
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise DomainError("Snapshot contains a non-finite attribute", kind="invalid_snapshot")
    if isinstance(value, (str, int, float, bool)):
        return value
    raise DomainError("Snapshot contains an unsupported attribute", kind="invalid_snapshot")


def _row(dataset: dict, record) -> dict:
    result = {"id": str(record[dataset["internalIdField"]]),
              "values": {field["name"]: _value(record[field["name"]], field) for field in dataset["fields"]}}
    if _has_source_row(dataset):
        original = record[dataset["sourceFidField"]]
        result["sourceRow"] = str(original) if original is not None else None
    return result


def _has_source_row(dataset: dict) -> bool:
    return dataset.get("kind") == "table" or dataset.get("source", {}).get("driver") == "TablePoints"


def _size(result: dict) -> int:
    return len(json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii"))


def _bounded(result: dict) -> dict:
    if _size(result) > MAX_QUERY_BYTES:
        raise DomainError("Query exceeds the 2 MiB response limit; reduce the requested page or fields", kind="query_limit")
    return result


def _filter(dataset: dict, value) -> tuple[str, list]:
    if value is None:
        return "", []
    if not isinstance(value, dict) or set(value) != {"field", "operator", "value"}:
        raise InvalidParamsError("filter must contain field, operator and value")
    name, operator, operand = value["field"], value["operator"], value["value"]
    known_fields = _field_names(dataset)
    if not isinstance(name, str) or name not in known_fields:
        raise InvalidParamsError("filter field is not in the dataset")
    if not isinstance(operand, str) or len(operand) > 4096:
        raise InvalidParamsError("filter value must be a string of at most 4096 characters")
    field = _quote(name)
    if operator == "isNull":
        return f" WHERE {field} IS NULL", []
    if operator == "equals":
        if known_fields[name]["storageType"] == "bool":
            booleans = {"true": 1, "false": 0, "1": 1, "0": 0}
            normalized = operand.strip().lower()
            if normalized not in booleans:
                raise InvalidParamsError("Boolean equality requires true, false, 1 or 0")
            return f" WHERE {field} = ?", [booleans[normalized]]
        return f" WHERE {field} = ?", [operand]
    if operator == "contains":
        if known_fields[name]["storageType"] == "bool":
            displayed = f"CASE WHEN {field} IS NULL THEN NULL WHEN {field} = 0 THEN 'false' ELSE 'true' END"
            return f" WHERE instr({displayed}, ?) > 0", [operand]
        return f" WHERE instr(CAST({field} AS TEXT), ?) > 0", [operand]
    raise InvalidParamsError("Unsupported filter operator")


def _certify_page_key(connection, dataset: dict) -> tuple[str | None, int | None]:
    """Prove ordinal == physical key under this query's transaction and deadline.

    This deliberately scans on every default page: no cross-request proof cache and
    no changes to immutable snapshots. Historical layouts can retain the old sort.
    """
    table = _quote(dataset["storageLayer"])
    primary = [row for row in connection.execute(f"PRAGMA table_info({table})") if row["pk"]]
    if len(primary) != 1 or primary[0]["type"].upper() != "INTEGER":
        return None, None
    key = _quote(primary[0]["name"])
    identifier = _quote(dataset["internalIdField"])
    count, minimum, maximum, mismatches = connection.execute(
        f"SELECT COUNT(*), MIN({key}), MAX({key}), SUM(CASE WHEN "
        f"typeof({identifier}) = 'text' AND typeof({key}) = 'integer' "
        f"AND {identifier} COLLATE BINARY = CAST({key} AS TEXT) THEN 0 ELSE 1 END) FROM {table}"
    ).fetchone()
    contiguous = (count == 0 and minimum is None and maximum is None) or (
        count > 0 and minimum == 1 and maximum == count and mismatches == 0)
    if contiguous and count == dataset.get("featureCount"):
        return key, count
    return None, count


def attribute_page(dataset: dict, managed_path: Path, params: dict) -> dict:
    offset = _integer(params.get("offset", 0), "offset", 0, MAX_FEATURES)
    limit = _integer(params.get("limit", 200), "limit", 1, 500)
    sort_field = params.get("sortField")
    descending = params.get("descending", False)
    if not isinstance(descending, bool):
        raise InvalidParamsError("descending must be a boolean")
    if sort_field is not None and (not isinstance(sort_field, str) or sort_field not in _field_names(dataset)):
        raise InvalidParamsError("sortField is not in the dataset")
    where, values = _filter(dataset, params.get("filter"))
    identifier = _quote(dataset["internalIdField"])
    order = f"CAST({identifier} AS INTEGER) ASC"
    if sort_field is not None:
        quoted = _quote(sort_field)
        order = f"({quoted} IS NULL) ASC, {quoted} {'DESC' if descending else 'ASC'}, " + order
    names = [dataset["internalIdField"], *[field["name"] for field in dataset["fields"]]]
    if _has_source_row(dataset):
        names.append(dataset["sourceFidField"])
    columns = ", ".join(_quote(name) for name in dict.fromkeys(names))
    table = _quote(dataset["storageLayer"])
    try:
        with closing(ArtifactLease(managed_path)) as lease:
            with closing(_connect(managed_path)) as connection:
                connection.execute("BEGIN")
                key, total = _certify_page_key(connection, dataset) if not where and sort_field is None else (None, None)
                if total is None:
                    total = connection.execute(f"SELECT COUNT(*) FROM {table}{where}", values).fetchone()[0]
                if key is not None:
                    records = connection.execute(f"SELECT {columns} FROM {table} WHERE {key} > ? ORDER BY {key} LIMIT ?", (offset, limit)).fetchall()
                else:
                    records = connection.execute(f"SELECT {columns} FROM {table}{where} ORDER BY {order} LIMIT ? OFFSET ?", [*values, limit, offset]).fetchall()
            result = _bounded({"datasetId": dataset["id"], "version": dataset["version"], "fields": dataset["fields"],
                              "rows": [_row(dataset, row) for row in records], "total": total, "offset": offset, "limit": limit,
                              "hasMore": offset + len(records) < total, "truncated": False})
            lease.require_identity(managed_path)
            return result
    except (sqlite3.Error, OSError) as exc:
        if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT:
            raise DomainError("Attribute query exceeded its time budget", kind="query_timeout") from exc
        raise DomainError("Could not read snapshot attributes", kind="query_failed", detail=str(exc)) from exc


def _bbox(value) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise InvalidParamsError("bbox must contain four WGS84 coordinates")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise InvalidParamsError("bbox coordinates must be numbers")
    try:
        result = [float(item) for item in value]
    except (ValueError, OverflowError) as exc:
        raise InvalidParamsError("bbox coordinates must be finite") from exc
    if not all(math.isfinite(item) for item in result) or not (-180 <= result[0] <= result[2] <= 180 and -90 <= result[1] <= result[3] <= 90):
        raise InvalidParamsError("bbox must be ordered and inside WGS84 longitude/latitude bounds")
    return result


def _display_geometry(geometry, transformer):
    try:
        result = shapely.transform(geometry, lambda x, y: transformer.transform(x, y, errcheck=True), interleaved=False)
        if not all(math.isfinite(value) for pair in shapely.get_coordinates(result) for value in pair):
            raise ValueError("non-finite display coordinates")
        return result
    except Exception as exc:
        raise DomainError("Geometry could not be transformed for display", kind="display_transform_failed", detail=str(exc)) from exc


def _display_feature(identifier: str, geometry, properties: dict) -> dict:
    return {"type": "Feature", "id": identifier, "properties": properties,
            "geometry": None if geometry is None else json.loads(shapely.to_geojson(geometry))}


def viewport(dataset: dict, managed_path: Path, params: dict) -> dict:
    if dataset.get("kind") != "vector":
        raise DomainError("Table datasets have no map geometry", kind="dataset_not_spatial")
    bounds = _bbox(params.get("bbox"))
    limit = _integer(params.get("limit", MAX_DISPLAY_FEATURES), "limit", 1, MAX_DISPLAY_FEATURES)
    requested = params.get("propertyFields", [])
    known_fields = _field_names(dataset)
    if not isinstance(requested, list) or len(requested) > 3 or any(not isinstance(name, str) or name not in known_fields for name in requested):
        raise InvalidParamsError("propertyFields must contain at most three known fields")
    if not dataset["crsWkt"]:
        raise DomainError("Assign the source CRS before displaying this dataset", kind="crs_required")
    to_source = Transformer.from_crs(4326, dataset["crsWkt"], always_xy=True)
    to_display = Transformer.from_crs(dataset["crsWkt"], 4326, always_xy=True)
    try:
        source_bounds = to_source.transform_bounds(*bounds, densify_pts=21, errcheck=True)
        if not all(math.isfinite(value) for value in source_bounds):
            raise ValueError("non-finite source extent")
    except Exception as exc:
        raise DomainError("Viewport cannot be transformed into the source CRS", kind="display_transform_failed", detail=str(exc)) from exc
    result = {"datasetId": dataset["id"], "version": dataset["version"], "bbox": bounds, "dataCrs": "EPSG:4326",
              "collection": {"type": "FeatureCollection", "features": []}, "truncated": False, "returnedCount": 0}
    viewport_box, vertex_count = shapely.box(*bounds), 0
    features = result["collection"]["features"]
    envelope_bytes, feature_bytes = _size(result) + 16, 0
    columns = list(dict.fromkeys([dataset["internalIdField"], *requested]))
    try:
        with pyogrio.open_arrow(managed_path, layer=dataset["storageLayer"], columns=columns, bbox=source_bounds,
                                batch_size=256, use_pyarrow=True) as (meta, reader):
            geometry_name = meta["geometry_name"] or "wkb_geometry"
            exhausted = False
            for batch in reader:
                geometries = _decode_geometry(batch[geometry_name].to_pylist())
                for index, geometry in enumerate(geometries):
                    if geometry is None or shapely.is_empty(geometry):
                        continue
                    if shapely.get_num_coordinates(geometry) > MAX_GEOMETRY_VERTICES:
                        raise DomainError("Feature exceeds the display vertex limit", kind="query_limit")
                    display = _display_geometry(geometry, to_display)
                    if not shapely.intersects(display, viewport_box):
                        continue
                    vertices = int(shapely.get_num_coordinates(display))
                    if len(features) >= limit or vertex_count + vertices > MAX_DISPLAY_VERTICES:
                        result["truncated"], exhausted = True, True
                        break
                    properties = {name: _value(batch[name][index].as_py(), known_fields[name]) for name in requested}
                    candidate = _display_feature(str(batch[dataset["internalIdField"]][index].as_py()), display, properties)
                    candidate_bytes = _size(candidate) + bool(features)
                    if envelope_bytes + feature_bytes + candidate_bytes > MAX_QUERY_BYTES:
                        result["truncated"], exhausted = True, True
                        break
                    features.append(candidate)
                    feature_bytes += candidate_bytes
                    vertex_count += vertices
                    result["returnedCount"] = len(features)
                if exhausted:
                    break
        return _bounded(result)
    except (DomainError, InvalidParamsError):
        raise
    except Exception as exc:
        raise DomainError("Could not query the vector viewport", kind="query_failed", detail=str(exc)) from exc


def feature(dataset: dict, managed_path: Path, params: dict) -> dict:
    if dataset.get("kind") != "vector":
        raise DomainError("Table datasets have no map geometry", kind="dataset_not_spatial")
    feature_id = params.get("featureId")
    if not isinstance(feature_id, str) or not feature_id.isascii() or not feature_id.isdecimal() or len(feature_id) > 10:
        raise InvalidParamsError("featureId must be a stored feature identifier")
    info = pyogrio.read_info(managed_path, layer=dataset["storageLayer"])
    fid_name = info["fid_column"]
    columns = list(dict.fromkeys([fid_name, dataset["internalIdField"], *[field["name"] for field in dataset["fields"]]]))
    if _has_source_row(dataset) and dataset["sourceFidField"] not in columns:
        columns.append(dataset["sourceFidField"])
    try:
        with closing(_connect(managed_path)) as connection:
            record = connection.execute(f"SELECT {', '.join(_quote(name) for name in columns)} FROM {_quote(dataset['storageLayer'])} WHERE {_quote(dataset['internalIdField'])} = ?", [feature_id]).fetchone()
        if record is None:
            raise DomainError("Feature does not exist in this dataset version", kind="feature_not_found")
        row = _row(dataset, record)
        display, bounds = None, None
        if dataset["crsWkt"]:
            meta, table = pyogrio.read_arrow(managed_path, layer=dataset["storageLayer"], fids=[int(record[fid_name])], columns=[])
            geometry = _decode_geometry(table[meta["geometry_name"] or "wkb_geometry"].to_pylist())[0]
            if geometry is not None and not shapely.is_empty(geometry):
                if shapely.get_num_coordinates(geometry) > MAX_DISPLAY_VERTICES:
                    raise DomainError("Feature exceeds the display vertex limit", kind="query_limit")
                transformer = Transformer.from_crs(dataset["crsWkt"], 4326, always_xy=True)
                transformed = _display_geometry(geometry, transformer)
                display = _display_feature(feature_id, transformed, row["values"])
                bounds = [float(value) for value in shapely.bounds(transformed)]
        return _bounded({"datasetId": dataset["id"], "version": dataset["version"], "row": row, "feature": display, "boundsWgs84": bounds})
    except (DomainError, InvalidParamsError):
        raise
    except Exception as exc:
        raise DomainError("Could not retrieve vector feature", kind="query_failed", detail=str(exc)) from exc
