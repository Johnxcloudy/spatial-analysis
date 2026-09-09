from __future__ import annotations

import hashlib
import math
import struct
import uuid
import warnings
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyogrio
import shapely
from pyproj import CRS, Transformer

from .errors import DomainError, InvalidParamsError
from .validation import require_crs, require_exact_keys, require_path, require_string

MAX_FEATURES = 100_000
MAX_VERTICES = 2_000_000
MAX_FIELDS = 256
MAX_LAYERS = 512
BATCH_SIZE = 2_048
DRIVERS = {".gpkg": "GPKG", ".shp": "ESRI Shapefile", ".geojson": "GeoJSON", ".json": "GeoJSON", ".gdb": "OpenFileGDB"}
SIMPLE_TYPES = {"Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon", "GeometryCollection", "Unknown"}


def _cancel(cancelled) -> None:
    if cancelled():
        raise DomainError("Task was cancelled", kind="task_cancelled")


def _source_path(value: Any) -> Path:
    path = require_path(value, "sourcePath")
    if path.suffix.lower() not in DRIVERS or not path.exists():
        raise DomainError("Source must be an existing GPKG, SHP, GeoJSON or GDB", kind="unsupported_source")
    if path.suffix.lower() == ".gdb":
        if not path.is_dir():
            raise DomainError("File Geodatabase must be a directory", kind="invalid_source")
    elif not path.is_file():
        raise DomainError("Source must be a file", kind="invalid_source")
    if path.suffix.lower() == ".shp":
        available = {item.name.casefold() for item in path.parent.iterdir()}
        missing = [suffix for suffix in (".shp", ".shx", ".dbf") if (path.stem + suffix).casefold() not in available]
        if missing:
            raise DomainError("Shapefile is missing required components", kind="missing_components", detail=", ".join(missing))
    return path


def _encoding(value: Any) -> str | None:
    if value is None:
        return None
    raw = require_string(value, "encoding", maximum=32)
    if raw.upper().replace("-", "") not in {"UTF8", "GBK", "GB18030"}:
        raise InvalidParamsError("encoding must be UTF-8, GBK or GB18030")
    return raw


def source_fingerprint(path: Path, cancelled=lambda: False) -> str:
    if path.is_dir():
        files = sorted((item for item in path.rglob("*") if item.is_file()), key=lambda item: item.relative_to(path).as_posix())
        root = path
    elif path.suffix.lower() == ".shp":
        files = sorted((item for item in path.parent.iterdir() if item.is_file() and item.stem.casefold() == path.stem.casefold()), key=lambda item: item.name.casefold())
        root = path.parent
    else:
        files, root = [path], path.parent
    digest = hashlib.sha256()
    for item in files:
        _cancel(cancelled)
        digest.update(item.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        with item.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                _cancel(cancelled)
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _content_hash(path: Path, cancelled=lambda: False) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            _cancel(cancelled)
            digest.update(chunk)
    return digest.hexdigest()


def _crs(value: Any) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    crs = CRS.from_user_input(value)
    authority = crs.to_authority()
    return crs.to_wkt(), ":".join(authority) if authority else None


def _bounds(value: Any) -> list[float] | None:
    if value is None or len(value) != 4 or not all(math.isfinite(float(item)) for item in value):
        return None
    return [float(item) for item in value]


def wgs84_bounds(bounds, crs_wkt) -> list[float] | None:
    if bounds is None or crs_wkt is None:
        return None
    try:
        transformed = Transformer.from_crs(crs_wkt, 4326, always_xy=True).transform_bounds(*bounds, densify_pts=21, errcheck=True)
        result = _bounds(transformed)
        if result and -180 <= result[0] <= result[2] <= 180 and -90 <= result[1] <= result[3] <= 90:
            return result
    except Exception:
        pass
    return None


def _field(name: str, source_type: str, storage_type: str | None = None, nullable=None) -> dict:
    return {"name": name, "sourceType": source_type, "storageType": storage_type or source_type,
            "nullable": nullable, "alias": None, "width": None, "precision": None, "metadataStatus": "partial"}


def inspect_source(params: dict) -> dict:
    require_exact_keys(params, {"sourcePath", "encoding"})
    path, encoding = _source_path(params["sourcePath"]), _encoding(params["encoding"])
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            available = pyogrio.list_layers(path)
            if len(available) > MAX_LAYERS:
                raise DomainError("Source exceeds the 512 layer limit", kind="source_limit")
            layers = []
            driver = DRIVERS[path.suffix.lower()]
            for name, geometry_type in available:
                info = pyogrio.read_info(path, layer=name, encoding=encoding)
                driver = info["driver"]
                crs_wkt, authority = _crs(info["crs"])
                layers.append({"name": str(name), "geometryType": geometry_type,
                               "featureCount": int(info["features"]) if info["features"] >= 0 else None,
                               "crsWkt": crs_wkt, "crsAuthority": authority,
                               "fields": [_field(str(field), str(dtype)) for field, dtype in zip(info["fields"], info["dtypes"])],
                               "bounds": _bounds(info["total_bounds"])})
        if driver != DRIVERS[path.suffix.lower()]:
            raise DomainError("Source contents do not match the supported file format", kind="unsupported_source")
        return {"sourcePath": str(path), "driver": driver, "layers": layers,
                "warnings": [str(item.message) for item in caught] + ["Field aliases, domains, relationships and precision metadata are not fully read."]}
    except (DomainError, InvalidParamsError):
        raise
    except Exception as exc:
        raise DomainError("Could not inspect vector source", kind="source_read_failed", detail=str(exc)) from exc


def _unique_name(base: str, names: set[str]) -> str:
    candidate, index = base, 1
    while candidate.casefold() in names:
        candidate = f"{base}_{index}"
        index += 1
    names.add(candidate.casefold())
    return candidate


def _check_geometry_type(name: str | None) -> None:
    if name is None or name not in SIMPLE_TYPES:
        raise DomainError("Only ordinary two-dimensional vector geometry is supported", kind="unsupported_geometry", detail=str(name))


def _decode_geometry(values: list[bytes | None]):
    # Inspect WKB before GEOS decoding: accepting a linearized dimensional type would lose source information.
    for value in values:
        if value is None:
            continue
        if len(value) < 5 or value[0] not in (0, 1):
            raise DomainError("Source contains unreadable geometry", kind="invalid_geometry")
        type_code = struct.unpack("<I" if value[0] else ">I", value[1:5])[0]
        if type_code & 0xC0000000 or (type_code & 0x0FFFFFFF) >= 1000 or (type_code & 0xFF) not in range(1, 8):
            raise DomainError("Z, M and curved geometry cannot be imported without loss", kind="unsupported_geometry")
    try:
        geometries = shapely.from_wkb(values, on_invalid="raise")
    except Exception as exc:
        raise DomainError("Source geometry cannot be decoded without repair", kind="invalid_geometry", detail=str(exc)) from exc
    if np.any(shapely.has_z(geometries)) or np.any(shapely.has_m(geometries)):
        raise DomainError("Z and M geometry are not supported", kind="unsupported_geometry")
    coordinates = shapely.get_coordinates(geometries)
    if not np.isfinite(coordinates).all():
        raise DomainError("Source contains non-finite coordinates", kind="invalid_geometry")
    return geometries


def _supported_field(field: pa.Field) -> bool:
    kind = field.type
    return any(check(kind) for check in (pa.types.is_integer, pa.types.is_floating, pa.types.is_boolean,
                                        pa.types.is_string, pa.types.is_large_string, pa.types.is_date, pa.types.is_timestamp))


def _compatible_storage_type(source: pa.DataType, stored: pa.DataType) -> bool:
    if source == stored:
        return True
    if pa.types.is_integer(source) and pa.types.is_integer(stored):
        return stored.bit_width >= source.bit_width and (pa.types.is_signed_integer(stored) == pa.types.is_signed_integer(source))
    return False


def canonical_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def verify_snapshot(path: Path, dataset: dict, expected: pa.Table | None = None, geometry_name: str | None = None, cancelled=lambda: False) -> None:
    _cancel(cancelled)
    if expected is None and _content_hash(path, cancelled) != dataset["version"]:
        raise DomainError("Managed snapshot changed after import", kind="snapshot_changed")
    info = pyogrio.read_info(path, layer=dataset["storageLayer"], force_feature_count=True)
    if info["driver"] != "GPKG" or info["features"] != dataset["featureCount"]:
        raise DomainError("Snapshot count or format did not round trip", kind="roundtrip_failed")
    if info["geometry_type"] != dataset["geometryType"]:
        raise DomainError("Snapshot geometry type does not match registered metadata", kind="roundtrip_failed")
    if dataset["crsWkt"]:
        if not info["crs"] or not CRS.from_user_input(info["crs"]).equals(CRS.from_user_input(dataset["crsWkt"])):
            raise DomainError("Snapshot CRS did not round trip", kind="roundtrip_failed")
    meta, actual = pyogrio.read_arrow(path, layer=dataset["storageLayer"])
    actual_geometry = meta["geometry_name"] or "wkb_geometry"
    expected_fields = {field["name"] for field in dataset["fields"]} | {dataset["internalIdField"], dataset["sourceFidField"]}
    if set(info["fields"]) != expected_fields:
        raise DomainError("Snapshot fields do not match registered metadata", kind="roundtrip_failed")
    for field in dataset["fields"]:
        stored = actual.schema.field(field["name"])
        if expected is not None:
            source = expected.schema.field(field["name"])
            if not _compatible_storage_type(source.type, stored.type):
                raise DomainError("Snapshot field type changed during conversion", kind="roundtrip_failed", detail=field["name"])
            if source.type != stored.type or source.nullable != stored.nullable:
                dataset["report"]["warnings"].append(f"Storage schema changed for {field['name']}: {source.type}/{source.nullable} -> {stored.type}/{stored.nullable}; values and NULLs were verified.")
            field["storageType"], field["nullable"] = str(stored.type), stored.nullable
        elif str(stored.type) != field["storageType"] or stored.nullable != field["nullable"]:
            raise DomainError("Snapshot field schema does not match registered metadata", kind="roundtrip_failed", detail=field["name"])
    actual_geometries = _decode_geometry(actual[actual_geometry].to_pylist())
    finite = shapely.bounds(actual_geometries[~shapely.is_missing(actual_geometries) & ~shapely.is_empty(actual_geometries)])
    actual_bounds = [float(finite[:, 0].min()), float(finite[:, 1].min()), float(finite[:, 2].max()), float(finite[:, 3].max())] if len(finite) else None
    if actual_bounds != dataset["bounds"]:
        raise DomainError("Snapshot bounds do not match registered metadata", kind="roundtrip_failed")
    ids = actual[dataset["internalIdField"]].to_pylist()
    if ids != [str(index + 1) for index in range(dataset["featureCount"])]:
        raise DomainError("Snapshot feature identities are invalid", kind="roundtrip_failed")
    if not pa.types.is_string(actual[dataset["sourceFidField"]].type):
        raise DomainError("Snapshot source FID mapping is invalid", kind="roundtrip_failed")
    if expected is not None:
        for column in expected.column_names:
            _cancel(cancelled)
            if column == geometry_name:
                expected_geometries = _decode_geometry(expected[column].to_pylist())
                matches = shapely.equals_exact(expected_geometries, actual_geometries, tolerance=0.0)
                matches |= shapely.is_missing(expected_geometries) & shapely.is_missing(actual_geometries)
                if not np.all(matches):
                    raise DomainError("Snapshot geometry changed during conversion", kind="roundtrip_failed")
            else:
                if column not in actual.column_names or [canonical_value(v) for v in expected[column].to_pylist()] != [canonical_value(v) for v in actual[column].to_pylist()]:
                    raise DomainError("Snapshot attributes changed during conversion", kind="roundtrip_failed", detail=column)


def import_vector(payload: dict, work_dir: Path, progress, cancelled) -> dict:
    require_exact_keys(payload, {"sourcePath", "sourceLayer", "encoding", "assignedCrs", "datasetId"})
    path, encoding = _source_path(payload["sourcePath"]), _encoding(payload["encoding"])
    layer = require_string(payload["sourceLayer"], "sourceLayer", maximum=512)
    dataset_id = require_string(payload["datasetId"], "datasetId", maximum=64)
    try:
        uuid.UUID(dataset_id)
    except ValueError as exc:
        raise InvalidParamsError("datasetId must be a UUID") from exc
    assigned = require_crs(payload["assignedCrs"], "assignedCrs", nullable=True)
    _cancel(cancelled)
    progress("fingerprinting", None, None)
    fingerprint = source_fingerprint(path, cancelled)
    inspection = inspect_source({"sourcePath": str(path), "encoding": encoding})
    matching = next((item for item in inspection["layers"] if item["name"] == layer), None)
    if matching is None:
        raise DomainError("Selected source layer does not exist", kind="layer_not_found")
    _check_geometry_type(matching["geometryType"])
    if matching["crsWkt"] and assigned is not None:
        raise DomainError("An existing source CRS cannot be overridden", kind="crs_override")
    if len(matching["fields"]) > MAX_FIELDS or (matching["featureCount"] or 0) > MAX_FEATURES:
        raise DomainError("Source exceeds the feature or field limit", kind="source_limit")
    crs_wkt, authority = _crs(matching["crsWkt"] or assigned)
    names = {field["name"].casefold() for field in matching["fields"]}
    if len(names) != len(matching["fields"]):
        raise DomainError("Field names collide under SQLite case rules", kind="unsupported_fields")
    if any("\ufffd" in field["name"] for field in matching["fields"]):
        raise DomainError("Source field names contain replacement characters", kind="encoding_error")
    id_field, source_fid_field = _unique_name("_sa_id", names), _unique_name("_sa_source_fid", names)
    output_geometry = _unique_name("_sa_geometry", names)
    counts = {"features": 0, "vertices": 0, "invalid": 0, "empty": 0, "missing": 0, "duplicateGeometry": 0}
    batches, seen = [], set()
    bounds = None
    fields = matching["fields"]
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pyogrio.open_arrow(path, layer=layer, encoding=encoding, return_fids=True, batch_size=BATCH_SIZE, use_pyarrow=True) as (meta, reader):
                geometry_name = meta["geometry_name"] or "wkb_geometry"
                fid_name = meta["fid_column"]
                schema = reader.schema
                source_fields = list(meta["fields"])
                for field_name in source_fields:
                    if not _supported_field(schema.field(field_name)):
                        raise DomainError("Attribute type cannot be preserved by this milestone", kind="unsupported_fields", detail=field_name)
                fields = [_field(field["name"], field["sourceType"], str(schema.field(field["name"]).type), schema.field(field["name"]).nullable) for field in fields]
                for batch in reader:
                    _cancel(cancelled)
                    geometries = _decode_geometry(batch[geometry_name].to_pylist())
                    start = counts["features"]
                    counts["features"] += len(batch)
                    counts["vertices"] += int(np.sum(shapely.get_num_coordinates(geometries)))
                    if counts["features"] > MAX_FEATURES or counts["vertices"] > MAX_VERTICES:
                        raise DomainError("Source exceeds 100000 features or 2000000 vertices", kind="source_limit")
                    missing, empty = shapely.is_missing(geometries), shapely.is_empty(geometries)
                    counts["missing"] += int(np.sum(missing))
                    counts["empty"] += int(np.sum(empty))
                    counts["invalid"] += int(np.sum(~shapely.is_valid(geometries) & ~missing & ~empty))
                    for value in batch[geometry_name].to_pylist():
                        if value:
                            digest = hashlib.sha256(value).digest()
                            counts["duplicateGeometry"] += digest in seen
                            seen.add(digest)
                    finite_bounds = shapely.bounds(geometries[~missing & ~empty])
                    if len(finite_bounds):
                        part = [float(finite_bounds[:, 0].min()), float(finite_bounds[:, 1].min()), float(finite_bounds[:, 2].max()), float(finite_bounds[:, 3].max())]
                        bounds = part if bounds is None else [min(bounds[0], part[0]), min(bounds[1], part[1]), max(bounds[2], part[2]), max(bounds[3], part[3])]
                    table = pa.Table.from_batches([batch]).select(source_fields)
                    for name in source_fields:
                        if pa.types.is_floating(table[name].type) and any(value is not None and not math.isfinite(value) for value in table[name].to_pylist()):
                            raise DomainError("Non-finite attributes cannot be preserved", kind="unsupported_fields", detail=name)
                        if (pa.types.is_string(table[name].type) or pa.types.is_large_string(table[name].type)) and any(value is not None and "\ufffd" in value for value in table[name].to_pylist()):
                            raise DomainError("Source text contains replacement characters; check its encoding", kind="encoding_error", detail=name)
                    table = table.append_column(id_field, pa.array([str(start + index + 1) for index in range(len(batch))]))
                    table = table.append_column(source_fid_field, pa.array([str(value) if value is not None else None for value in batch[fid_name].to_pylist()], type=pa.string()))
                    table = table.append_column(output_geometry, batch[geometry_name])
                    batches.append(table)
                    progress("reading", counts["features"], matching["featureCount"])
                if not batches:
                    table = pa.Table.from_batches([], schema=schema).select(source_fields)
                    table = table.append_column(id_field, pa.array([], type=pa.string())).append_column(source_fid_field, pa.array([], type=pa.string()))
                    table = table.append_column(output_geometry, pa.array([], type=pa.binary()))
                    batches.append(table)
            warning_text = [str(item.message) for item in caught]
        if any(any(term in item.lower() for term in ("measured", "curve", "lineariz", "3d", "unsupported", "convert")) for item in warning_text):
            raise DomainError("Source driver reported a potentially lossy conversion", kind="unsupported_geometry", detail="; ".join(warning_text))
        table = pa.concat_tables(batches)
        work_dir.mkdir(parents=True, exist_ok=True)
        artifact = (work_dir / "snapshot.gpkg").resolve()
        if artifact.exists():
            raise DomainError("Snapshot destination already exists", kind="destination_exists")
        progress("writing", counts["features"], counts["features"])
        _cancel(cancelled)
        with warnings.catch_warnings(record=True) as write_warnings:
            warnings.simplefilter("always")
            pyogrio.write_arrow(table, artifact, layer="features", driver="GPKG", geometry_name=output_geometry,
                                geometry_type=matching["geometryType"], crs=crs_wkt,
                                layer_options={"SPATIAL_INDEX": "YES", "GEOMETRY_NAME": output_geometry, "FID": _unique_name("_sa_fid", names)})
        warning_text.extend(str(item.message) for item in write_warnings)
        projected_bounds = wgs84_bounds(bounds, crs_wkt)
        restricted = crs_wkt is None or (bounds is not None and projected_bounds is None) or any(counts[key] for key in ("missing", "empty", "invalid"))
        dataset = {"id": dataset_id, "version": str(uuid.uuid4()), "name": layer, "kind": "vector",
                   "source": {"path": str(path), "layer": layer, "driver": inspection["driver"], "fingerprint": fingerprint,
                              "encoding": encoding, "assignedCrs": assigned, "crsWkt": matching["crsWkt"], "metadata": {}},
                   "relativePath": f"datasets/{dataset_id}.gpkg", "storageLayer": "features", "featureCount": counts["features"],
                   "geometryType": matching["geometryType"], "crsWkt": crs_wkt, "crsAuthority": authority, "bounds": bounds,
                   "boundsWgs84": projected_bounds, "fields": fields, "internalIdField": id_field, "sourceFidField": source_fid_field,
                   "report": {"status": "restricted" if restricted else "warning", "checks": [],
                              "warnings": inspection["warnings"] + warning_text,
                              "notChecked": ["Survey positional accuracy", "Cross-feature topology and land-class overlaps", "Field domains, aliases, relationships and GDB-specific behavior"],
                              "counts": counts, "validatorVersion": "1"}, "createdAt": datetime.now(UTC).isoformat()}
        info = pyogrio.read_info(path, layer=layer, encoding=encoding)
        dataset["source"]["metadata"] = {str(key): str(value) for section in (info.get("dataset_metadata"), info.get("layer_metadata")) for key, value in (section or {}).items()}
        dataset["source"]["metadata"].update({"encodingSelected": encoding or "driver detection", "reportedEncoding": str(info.get("encoding"))})
        if path.suffix.lower() == ".shp":
            cpg = next((item for item in path.parent.iterdir() if item.stem.casefold() == path.stem.casefold() and item.suffix.lower() == ".cpg"), None)
            dataset["source"]["metadata"]["shapefile.cpg"] = cpg.read_text(encoding="utf-8-sig").strip() if cpg else "not supplied"
            dataset["report"]["warnings"].append("SHP reportedEncoding describes decoded text; original encoding comes from CPG or explicit selection and is not inferred from UTF-8 output.")
        if inspection["driver"] == "OpenFileGDB":
            dataset["report"]["notChecked"].append("GDB feature-dataset hierarchy, subtype/default rules, attachments and field widths/precision")
        if inspection["driver"] == "GeoJSON":
            dataset["report"]["warnings"].append("GeoJSON field types are inferred by GDAL; nested JSON objects and arrays are rejected by this importer.")
        progress("validating", counts["features"], counts["features"])
        verify_snapshot(artifact, dataset, table, output_geometry, cancelled)
        if source_fingerprint(path, cancelled) != fingerprint:
            raise DomainError("Source changed during import", kind="source_changed")
        dataset["version"] = _content_hash(artifact, cancelled)
        dataset["report"]["checks"] = [
            {"code": "roundtrip", "passed": True, "detail": "All attributes, IDs, geometries, count and CRS reread and compared"},
            {"code": "source_unchanged", "passed": True, "detail": "Source fingerprints match before and after import"},
            {"code": "geometry_valid", "passed": not any(counts[key] for key in ("invalid", "missing", "empty")), "detail": "Full geometry scan; no repair applied", "count": counts["invalid"]},
            {"code": "crs_known", "passed": crs_wkt is not None, "detail": "Source or explicitly assigned CRS"},
        ]
        return {"dataset": dataset, "artifactPath": str(artifact)}
    except (DomainError, InvalidParamsError):
        raise
    except Exception as exc:
        raise DomainError("Vector import failed", kind="vector_import_failed", detail=str(exc)) from exc


def export_vector(payload: dict, work_dir: Path, progress, cancelled) -> dict:
    dataset, managed = payload["dataset"], require_path(payload["managedPath"], "managedPath")
    _cancel(cancelled)
    progress("validating", None, None)
    verify_snapshot(managed, dataset, cancelled=cancelled)
    before = source_fingerprint(managed, cancelled)
    work_dir.mkdir(parents=True, exist_ok=True)
    artifact = (work_dir / "export.gpkg").resolve()
    progress("copying", 0, managed.stat().st_size)
    with managed.open("rb") as source, artifact.open("xb") as target:
        completed = 0
        while chunk := source.read(1024 * 1024):
            _cancel(cancelled)
            target.write(chunk)
            completed += len(chunk)
            progress("copying", completed, managed.stat().st_size)
    verify_snapshot(artifact, dataset, cancelled=cancelled)
    if source_fingerprint(managed, cancelled) != before or _content_hash(managed, cancelled) != _content_hash(artifact, cancelled):
        raise DomainError("Snapshot changed during export", kind="source_changed")
    return {"artifactPath": str(artifact)}
