"""Versioned, planar polygon analysis of complete immutable project snapshots."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyogrio
import pyproj
import shapely
from pyproj import CRS, Transformer, network
from pyproj.transformer import AreaOfInterest, TransformerGroup

from .errors import DomainError, InvalidParamsError
from .validation import require_exact_keys, require_object, require_string
from .vectors import _cancel, _content_hash, _decode_geometry, _field, wgs84_bounds

MAX_FEATURES = 500_000
MAX_VERTICES = 10_000_000
MAX_GEOMETRY_VERTICES = 100_000
MAX_BYTES = 512 * 1024 * 1024
MAX_CANDIDATES = 5_000_000
MAX_CLASSES = 10_000
BATCH_SIZE = 2048
OPTION_KEYS = {"operation", "name", "inputDatasetId", "overlayDatasetId", "inputClassField",
               "overlayClassField", "classificationStandard", "analysisCrs", "crsReason"}
OUTPUT_SCHEMA = pa.schema([("input_id", pa.string()), ("overlay_id", pa.string()),
                           ("input_class", pa.string()), ("overlay_class", pa.string()),
                           ("area_m2", pa.float64()), ("_sa_id", pa.string()),
                           ("_sa_source_fid", pa.string()), ("_sa_geometry", pa.binary())])


def _fail(message, kind="analysis_invalid", detail=None):
    raise DomainError(message, kind=kind, detail=detail)


def _target(value):
    try:
        crs = CRS.from_user_input(value)
    except Exception as exc:
        raise InvalidParamsError("analysisCrs is not a recognized CRS") from exc
    if (not crs.is_projected or crs.is_compound or len(crs.axis_info) != 2
            or crs.to_epsg() == 3857 or "pseudo" in (crs.coordinate_operation.method_name if crs.coordinate_operation else "").lower()
            or crs.area_of_use is None):
        _fail("Analysis CRS must be a suitable 2D projected CRS with a documented area of use", "analysis_crs")
    factors = [axis.unit_conversion_factor for axis in crs.axis_info]
    if any(value is None or not math.isfinite(value) or value <= 0 for value in factors) or factors[0] != factors[1]:
        _fail("Analysis CRS horizontal units are unsupported", "analysis_crs")
    return crs


def validate_options(params: dict, input_dataset: dict, overlay_dataset: dict) -> dict:
    require_object(params)
    require_exact_keys(params, OPTION_KEYS)
    options = dict(params)
    if options["operation"] not in ("clip", "intersect"):
        raise InvalidParamsError("operation must be clip or intersect")
    for key in ("name", "classificationStandard", "analysisCrs", "crsReason"):
        options[key] = require_string(options[key], key, maximum=8192 if key == "analysisCrs" else 200)
    _target(options["analysisCrs"])
    for dataset, id_key, class_key in ((input_dataset, "inputDatasetId", "inputClassField"),
                                        (overlay_dataset, "overlayDatasetId", "overlayClassField")):
        if options[id_key] != dataset["id"]:
            raise InvalidParamsError(f"{id_key} does not match the input")
        if dataset["kind"] != "vector" or dataset["geometryType"] not in {"Polygon", "MultiPolygon"}:
            _fail("Analysis requires Polygon or MultiPolygon datasets")
        if not dataset["crsWkt"]:
            _fail("Input CRS is unknown", "analysis_crs")
        if dataset["featureCount"] > MAX_FEATURES:
            _fail("Input exceeds the analysis feature budget", "analysis_limit")
        if class_key == "overlayClassField" and options["operation"] == "clip":
            if options[class_key] is not None:
                raise InvalidParamsError("clip requires overlayClassField=null")
            continue
        name = options[class_key]
        if not isinstance(name, str) or not name or len(name) > 256:
            raise InvalidParamsError(f"{class_key} must name a string field")
        field = next((field for field in dataset["fields"] if field["name"] == name), None)
        if field is None or field["storageType"].lower() not in {"string", "large_string", "string_view", "object"}:
            _fail("Classification field must be a preserved string field", "analysis_classification")
    return options


def _transformer(source, target, bounds):
    source = CRS.from_user_input(source)
    if source.is_compound or len(source.axis_info) != 2:
        _fail("Input CRS must be two-dimensional", "analysis_crs")
    network.set_network_enabled(False)
    area = target.area_of_use
    if bounds is None or not all(math.isfinite(v) for v in bounds):
        _fail("Input CRS geographic extent is unknown", "analysis_crs")
    west, south, east, north = bounds
    if area.west > area.east or not (area.west <= west <= east <= area.east and area.south <= south <= north <= area.north):
        _fail("Input extent is outside the analysis CRS area of use", "analysis_crs")
    group = TransformerGroup(source, target, always_xy=True, allow_ballpark=False,
                             area_of_interest=AreaOfInterest(west, south, east, north))
    if not group.best_available or not group.transformers:
        _fail("Required CRS operation or grid is unavailable; ballpark fallback is disabled", "analysis_crs")
    transformer = group.transformers[0]
    grids = [{"name": grid.short_name, "available": bool(grid.available)}
             for operation in transformer.operations for grid in operation.grids]
    if any(not grid["available"] for grid in grids):
        _fail("Required CRS grid is unavailable", "analysis_crs")
    record = {"sourceCrsWkt": source.to_wkt(), "targetCrsWkt": target.to_wkt(),
              "operation": transformer.definition, "accuracyM": transformer.accuracy if transformer.accuracy >= 0 else None,
              "grids": grids}
    return transformer, record


def _load(dataset, path, class_field, target, progress, cancelled):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        _fail("Input exceeds the analysis byte budget", "analysis_limit")
    if _content_hash(path, cancelled) != dataset["version"]:
        _fail("Input snapshot changed", "dataset_changed")
    info = pyogrio.read_info(path, layer=dataset["storageLayer"])
    if info["crs"] is None or not CRS.from_user_input(info["crs"]).equals(CRS.from_user_input(dataset["crsWkt"])):
        _fail("Input CRS metadata does not match snapshot", "analysis_crs")
    # PROJ transform_bounds can reject valid US-foot bounds; transform an explicit
    # densified perimeter to the source's own geographic CRS without datum fallback.
    source_crs = CRS.from_user_input(dataset["crsWkt"])
    xmin, ymin, xmax, ymax = info["total_bounds"]
    xs, ys = np.linspace(xmin, xmax, 23), np.linspace(ymin, ymax, 23)
    perimeter_x = np.concatenate((xs, xs, np.full(23, xmin), np.full(23, xmax)))
    perimeter_y = np.concatenate((np.full(23, ymin), np.full(23, ymax), ys, ys))
    geographic = Transformer.from_crs(source_crs, source_crs.geodetic_crs, always_xy=True, allow_ballpark=False)
    lon, lat = geographic.transform(perimeter_x, perimeter_y, errcheck=True)
    actual_bounds = [float(np.min(lon)), float(np.min(lat)), float(np.max(lon)), float(np.max(lat))]
    transformer, transform_record = _transformer(dataset["crsWkt"], target, actual_bounds)
    columns = list(dict.fromkeys([dataset["internalIdField"]] + ([class_field] if class_field else [])))
    geometries, ids, codes = [], [], []
    count_vertices = 0
    seen_ids = set()
    with pyogrio.open_arrow(path, layer=dataset["storageLayer"], columns=columns,
                            use_pyarrow=True, batch_size=BATCH_SIZE) as (meta, reader):
        geometry_name = meta["geometry_name"] or "wkb_geometry"
        if class_field and not (pa.types.is_string(reader.schema.field(class_field).type) or pa.types.is_large_string(reader.schema.field(class_field).type)):
            _fail("Classification field is not a string in snapshot", "analysis_classification")
        for batch in reader:
            _cancel(cancelled)
            values = _decode_geometry(batch[geometry_name].to_pylist())
            if np.any(shapely.is_missing(values) | shapely.is_empty(values) | ~shapely.is_valid(values)) or np.any(~np.isin(shapely.get_type_id(values), [3, 6])):
                _fail("Input contains empty, invalid or non-polygon geometry; no repair applied", "invalid_geometry")
            vertices = shapely.get_num_coordinates(values)
            count_vertices += int(vertices.sum())
            if np.any(vertices > MAX_GEOMETRY_VERTICES) or count_vertices > MAX_VERTICES or len(geometries) + len(values) > MAX_FEATURES:
                _fail("Input exceeds the analysis geometry budget", "analysis_limit")
            values = shapely.transform(values, lambda x, y: transformer.transform(x, y, errcheck=True), interleaved=False)
            if not np.isfinite(shapely.get_coordinates(values)).all() or np.any(~shapely.is_valid(values)):
                _fail("CRS transformation produced invalid geometry; no repair applied", "invalid_geometry")
            batch_ids = batch[dataset["internalIdField"]].to_pylist()
            for value in batch_ids:
                if not isinstance(value, str) or not value or value in seen_ids:
                    _fail("Input internal feature IDs are not unique strings")
                seen_ids.add(value)
            batch_codes = batch[class_field].to_pylist() if class_field else [None] * len(batch)
            if any(value is not None and (not isinstance(value, str) or len(value) > 1024) for value in batch_codes):
                _fail("Classification value exceeds the 1024-character analysis budget", "analysis_limit")
            geometries.extend(values)
            ids.extend(batch_ids)
            codes.extend(batch_codes)
            progress("reading_analysis_inputs", len(geometries), dataset["featureCount"])
    if len(geometries) != dataset["featureCount"] or not geometries:
        _fail("Input feature count is empty or inconsistent")
    return np.asarray(geometries, dtype=object), ids, codes, transform_record, count_vertices


class _Budget:
    def __init__(self):
        self.candidates = 0

    def add(self, count):
        self.candidates += count
        if self.candidates > MAX_CANDIDATES:
            _fail("Analysis candidate pair budget exceeded", "analysis_limit")


def _check_overlaps(geometries, ids, tree, budget, progress, cancelled):
    for index, geometry in enumerate(geometries):
        if index % 256 == 0:
            _cancel(cancelled)
            progress("checking_overlaps", index, len(geometries))
        candidates = tree.query(geometry)
        candidates = candidates[candidates > index]
        budget.add(len(candidates))
        for start in range(0, len(candidates), BATCH_SIZE):
            _cancel(cancelled)
            part = candidates[start:start + BATCH_SIZE]
            areas = shapely.area(shapely.intersection(geometry, geometries[part]))
            positive = np.flatnonzero(areas > 0)
            if len(positive):
                other = int(part[positive[0]])
                _fail("Input contains positive-area overlap; resolve it before analysis", "analysis_overlap",
                      json.dumps({"featureIds": [ids[index], ids[other]], "areaAnalysisUnits2": float(areas[positive[0]])}))


def _polygonal(geometry):
    if geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return geometry
    if geometry.geom_type == "GeometryCollection":
        parts = []
        for item in geometry.geoms:
            part = _polygonal(item)
            if part is not None:
                parts.extend(part.geoms if part.geom_type == "MultiPolygon" else [part])
        return shapely.MultiPolygon(parts) if parts else None
    return None


def _write_batch(path, rows, crs, first):
    table = pa.Table.from_pylist(rows, schema=OUTPUT_SCHEMA)
    pyogrio.write_arrow(table, path, layer="features", driver="GPKG", geometry_name="_sa_geometry",
                        geometry_type="MultiPolygon", crs=crs, append=not first,
                        layer_options={"SPATIAL_INDEX": "YES", "GEOMETRY_NAME": "_sa_geometry", "FID": "_sa_fid"} if first else None)
    if path.stat().st_size > MAX_BYTES:
        _fail("Analysis output byte budget exceeded", "analysis_limit")


def _digest_row(digest, row):
    values = [row[field.name] for field in OUTPUT_SCHEMA if field.name != "_sa_geometry"]
    encoded = json.dumps(values, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    geometry = shapely.to_wkb(shapely.normalize(shapely.from_wkb(row["_sa_geometry"])))
    digest.update(len(encoded).to_bytes(8, "little"))
    digest.update(encoded)
    digest.update(len(geometry).to_bytes(8, "little"))
    digest.update(geometry)


def _store_tables(path, record, rows):
    timestamp = record["createdAt"]
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE analysis_record (id INTEGER PRIMARY KEY, record_json TEXT NOT NULL)")
        db.execute("INSERT INTO analysis_record VALUES (1, ?)", (json.dumps(record, ensure_ascii=False, allow_nan=False),))
        db.execute("CREATE TABLE analysis_statistics (id INTEGER PRIMARY KEY, input_class TEXT, overlay_class TEXT, feature_count INTEGER NOT NULL, area_m2 REAL NOT NULL, area_ha REAL NOT NULL, area_mu REAL NOT NULL, study_ratio REAL NOT NULL, coverage_ratio REAL)")
        db.executemany("INSERT INTO analysis_statistics VALUES (?,?,?,?,?,?,?,?,?)", [(i + 1, row["inputClass"], row["overlayClass"], row["featureCount"], row["areaM2"], row["areaHa"], row["areaMu"], row["studyRatio"], row["coverageRatio"]) for i, row in enumerate(rows)])
        for name in ("analysis_record", "analysis_statistics"):
            db.execute("INSERT INTO gpkg_contents (table_name,data_type,identifier,description,last_change,srs_id) VALUES (?, 'attributes', ?, '', ?, NULL)", (name, name, timestamp))


def run_analysis(payload, work_dir, progress, cancelled):
    require_exact_keys(payload, {"taskId", "datasetId", "analysisId", "input", "overlay", "inputPath", "overlayPath", "options"})
    for key in ("taskId", "datasetId", "analysisId"):
        try:
            uuid.UUID(payload[key])
        except (ValueError, TypeError, AttributeError) as exc:
            raise InvalidParamsError(f"{key} must be a UUID") from exc
    options = validate_options(payload["options"], payload["input"], payload["overlay"])
    crs = _target(options["analysisCrs"])
    scale = crs.axis_info[0].unit_conversion_factor ** 2
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / "snapshot.gpkg"
    if path.exists():
        _fail("Analysis destination already exists", "destination_exists")
    _cancel(cancelled)
    left, left_ids, left_codes, left_transform, _ = _load(payload["input"], payload["inputPath"], options["inputClassField"], crs, progress, cancelled)
    right, right_ids, right_codes, right_transform, right_vertices = _load(payload["overlay"], payload["overlayPath"], options["overlayClassField"], crs, progress, cancelled)
    budget = _Budget()
    left_tree = shapely.STRtree(left)
    _check_overlaps(left, left_ids, left_tree, budget, progress, cancelled)
    del left_tree
    if options["operation"] == "clip":
        if len(right) > 10_000 or right_vertices > 1_000_000:
            _fail("Study boundary exceeds the union budget", "analysis_limit")
        _cancel(cancelled)
        boundary = shapely.union_all(right)
        study_area = float(boundary.area) * scale
        right = np.asarray([boundary], dtype=object)
        right_ids, right_codes = [None], [None]
    else:
        _check_overlaps(right, right_ids, shapely.STRtree(right), budget, progress, cancelled)
        study_area = math.fsum(float(area) * scale for area in shapely.area(right))
    if not math.isfinite(study_area) or study_area <= 0:
        _fail("Study area must have positive finite area")
    tree = shapely.STRtree(right)
    groups, rows, first = {}, [], True
    expected_digest = hashlib.sha256()
    buffered_bytes = 0
    output_count = output_vertices = contacts = 0
    bounds = None
    # A compensated accumulator per category avoids retaining one float per output.
    for index, geometry in enumerate(left):
        if index % 256 == 0:
            _cancel(cancelled)
            progress("intersecting", index, len(left))
        candidates = tree.query(geometry)
        budget.add(len(candidates))
        for start in range(0, len(candidates), BATCH_SIZE):
            _cancel(cancelled)
            part = candidates[start:start + BATCH_SIZE]
            intersections = shapely.intersection(geometry, right[part])
            for right_index, intersection in zip(part, intersections):
                if intersection.is_empty:
                    continue
                result = _polygonal(intersection)
                if result is None or result.area == 0:
                    contacts += 1
                    continue
                if not result.is_valid:
                    _fail("Intersection produced invalid geometry; no repair applied", "invalid_geometry")
                if result.geom_type == "Polygon":
                    result = shapely.MultiPolygon([result])
                output_count += 1
                vertex_count = int(shapely.get_num_coordinates(result))
                output_vertices += vertex_count
                if output_count > MAX_FEATURES or output_vertices > MAX_VERTICES or vertex_count > MAX_GEOMETRY_VERTICES:
                    _fail("Analysis output geometry budget exceeded", "analysis_limit")
                area = float(result.area) * scale
                if not math.isfinite(area):
                    _fail("Intersection area is non-finite", "invalid_geometry")
                key = (left_codes[index], right_codes[right_index])
                if key not in groups:
                    if len(groups) >= MAX_CLASSES:
                        _fail("Analysis category budget exceeded", "analysis_limit")
                    groups[key] = [0, 0.0, 0.0]
                accumulator = groups[key]
                accumulator[0] += 1
                corrected = area - accumulator[2]
                total = accumulator[1] + corrected
                accumulator[2] = (total - accumulator[1]) - corrected
                accumulator[1] = total
                b = result.bounds
                bounds = list(b) if bounds is None else [min(bounds[0], b[0]), min(bounds[1], b[1]), max(bounds[2], b[2]), max(bounds[3], b[3])]
                row = {"input_id": left_ids[index], "overlay_id": right_ids[right_index],
                             "input_class": key[0], "overlay_class": key[1], "area_m2": area,
                             "_sa_id": str(output_count), "_sa_source_fid": None, "_sa_geometry": shapely.to_wkb(result)}
                _digest_row(expected_digest, row)
                rows.append(row)
                buffered_bytes += len(row["_sa_geometry"]) + 4 * sum(len(value) for value in row.values() if isinstance(value, str))
                if len(rows) >= BATCH_SIZE or buffered_bytes >= 4 * 1024 * 1024:
                    _write_batch(path, rows, crs.to_wkt(), first)
                    rows, first = [], False
                    buffered_bytes = 0
    if rows or first:
        _write_batch(path, rows, crs.to_wkt(), first)
    covered_area = math.fsum(value[1] for value in groups.values())
    uncovered_area = study_area - covered_area
    # No clamp/repair: preserve any numerical residual and explain it in the record.
    created = datetime.now(UTC).isoformat()
    authority = crs.to_authority()
    authority = ":".join(authority) if authority else None
    record = {"schemaVersion": 1, "id": payload["analysisId"], "resultDatasetId": payload["datasetId"],
              "operation": options["operation"], "name": options["name"], "createdAt": created,
              "inputs": [{"datasetId": payload[k]["id"], "version": payload[k]["version"],
                          "classField": options[field], "featureCount": payload[k]["featureCount"], "crsWkt": payload[k]["crsWkt"]}
                         for k, field in (("input", "inputClassField"), ("overlay", "overlayClassField"))],
              "classificationStandard": options["classificationStandard"], "analysisCrsWkt": crs.to_wkt(),
              "analysisCrsAuthority": authority, "crsReason": options["crsReason"], "metresPerUnit": math.sqrt(scale),
              "areaMethod": "projected_planar", "overlapPolicy": "reject_positive_area",
              "geometryPolicy": "no_repair_no_snap_no_sliver_removal",
              "coverageExplanation": "All classified inputs passed strict positive-area overlap checks. Record sum equals unique coverage under this policy. The overlay union defines study area; uncovered area is study minus coverage. Floating residuals are not clamped.",
              "studyAreaM2": study_area, "recordAreaM2": covered_area, "coveredAreaM2": covered_area,
              "uncoveredAreaM2": uncovered_area, "outputFeatureCount": output_count, "boundaryContactCount": contacts,
              "categoryCount": len(groups), "candidatePairs": budget.candidates,
              "transforms": [left_transform, right_transform],
              "versions": {"algorithm": "1", "shapely": shapely.__version__, "geos": shapely.geos_version_string,
                           "pyproj": pyproj.__version__, "proj": pyproj.proj_version_str, "gdal": pyogrio.__gdal_version_string__},
              "warnings": ["Planar geometric area only; survey positional accuracy is unknown.",
                           "Only source feature IDs and class values are carried into output; original attributes remain in input snapshots."]}
    statistics = [{"inputClass": key[0], "overlayClass": key[1], "featureCount": value[0],
                   "areaM2": value[1], "areaHa": value[1] / 10_000, "areaMu": value[1] * 3 / 2000,
                   "studyRatio": value[1] / study_area, "coverageRatio": value[1] / covered_area if covered_area else None}
                  for key, value in sorted(groups.items(), key=lambda item: tuple((v is not None, v or "") for v in item[0]))]
    _store_tables(path, record, statistics)
    progress("validating_analysis", output_count, output_count)
    _cancel(cancelled)
    if path.stat().st_size > MAX_BYTES:
        _fail("Analysis output byte budget exceeded", "analysis_limit")
    # GDAL reread checks the real GeoPackage geometry/attribute layers before publication.
    actual_count = 0
    actual_digest = hashlib.sha256()
    with pyogrio.open_arrow(path, layer="features", use_pyarrow=True, batch_size=BATCH_SIZE) as (meta, reader):
        result_fields = [_field(field.name, str(OUTPUT_SCHEMA.field(field.name).type), str(field.type), field.nullable)
                         for field in reader.schema if not field.name.startswith("_sa_")]
        for batch in reader:
            _cancel(cancelled)
            values = _decode_geometry(batch[meta["geometry_name"] or "wkb_geometry"].to_pylist())
            if np.any(~shapely.is_valid(values)) or np.any(shapely.is_empty(values)):
                _fail("Result geometry roundtrip failed", "analysis_verification")
            stored_areas = np.asarray(batch["area_m2"].to_pylist())
            if not np.array_equal(shapely.area(values) * scale, stored_areas):
                _fail("Result area roundtrip failed", "analysis_verification")
            actual_count += len(batch)
            for row in batch.to_pylist():
                if (meta["geometry_name"] or "wkb_geometry") != "_sa_geometry":
                    row["_sa_geometry"] = row[meta["geometry_name"] or "wkb_geometry"]
                _digest_row(actual_digest, row)
    if actual_count != output_count or actual_digest.digest() != expected_digest.digest():
        _fail("Result geometry or attribute roundtrip failed", "analysis_verification")
    with sqlite3.connect(path) as db:
        if _read_record(db, {"id": payload["datasetId"], "source": {"metadata": {"analysisId": payload["analysisId"]}}}) != record:
            _fail("Analysis record roundtrip failed", "analysis_verification")
        actual_stats = db.execute("SELECT input_class,overlay_class,feature_count,area_m2,area_ha,area_mu,study_ratio,coverage_ratio FROM analysis_statistics ORDER BY id").fetchall()
        expected_stats = [tuple(row[name] for name in ("inputClass", "overlayClass", "featureCount", "areaM2", "areaHa", "areaMu", "studyRatio", "coverageRatio")) for row in statistics]
        if actual_stats != expected_stats:
            _fail("Analysis statistics roundtrip failed", "analysis_verification")
    for key, path_key in (("input", "inputPath"), ("overlay", "overlayPath")):
        if _content_hash(Path(payload[path_key]), cancelled) != payload[key]["version"]:
            _fail("Input snapshot changed during analysis", "dataset_changed")
    version = _content_hash(path, cancelled)
    metadata = {"analysisId": payload["analysisId"], "operation": options["operation"],
                "inputDatasetId": payload["input"]["id"], "inputVersion": payload["input"]["version"],
                "overlayDatasetId": payload["overlay"]["id"], "overlayVersion": payload["overlay"]["version"]}
    dataset = {"id": payload["datasetId"], "version": version, "name": options["name"], "kind": "vector",
               "source": {"path": payload["inputPath"], "layer": payload["input"]["storageLayer"], "driver": "SpatialAnalysis",
                          "fingerprint": hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest(),
                          "encoding": None, "assignedCrs": None, "crsWkt": payload["input"]["crsWkt"], "metadata": metadata},
               "relativePath": f"datasets/{payload['datasetId']}.gpkg", "storageLayer": "features", "featureCount": output_count,
               "geometryType": "MultiPolygon", "crsWkt": crs.to_wkt(), "crsAuthority": authority, "bounds": bounds,
               "boundsWgs84": wgs84_bounds(bounds, crs.to_wkt()), "fields": result_fields,
               "internalIdField": "_sa_id", "sourceFidField": "_sa_source_fid", "createdAt": created,
               "report": {"status": "warning", "checks": [{"code": "analysis_roundtrip", "passed": True, "detail": "GDAL reread all result geometries and area values"}],
                          "warnings": record["warnings"], "notChecked": ["Survey positional accuracy", "Legal/statutory area policies"],
                          "counts": {"features": output_count, "vertices": output_vertices, "invalid": 0, "empty": 0, "missing": 0}, "validatorVersion": "2"}}
    return {"dataset": dataset, "artifactPath": str(path.resolve())}


def _read_record(db, dataset):
    try:
        record = json.loads(db.execute("SELECT record_json FROM analysis_record WHERE id=1").fetchone()[0])
        if (record["schemaVersion"] != 1 or record["resultDatasetId"] != dataset["id"]
                or record["id"] != dataset["source"]["metadata"]["analysisId"]):
            _fail("Analysis record identity mismatch", "analysis_verification")
        return record
    except (sqlite3.Error, TypeError, ValueError, KeyError) as exc:
        raise DomainError("Analysis record is invalid", kind="analysis_verification", detail=str(exc)) from exc


def result_page(dataset, native_path, params):
    require_exact_keys(params, {"offset", "limit"})
    offset, limit = params["offset"], params["limit"]
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise InvalidParamsError("offset must be nonnegative and limit must be between 1 and 100")
    if dataset["source"]["driver"] != "SpatialAnalysis":
        _fail("Dataset is not an analysis result")
    with sqlite3.connect(Path(native_path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        record = _read_record(db, dataset)
        total = db.execute("SELECT COUNT(*) FROM analysis_statistics").fetchone()[0]
        rows = db.execute("SELECT input_class,overlay_class,feature_count,area_m2,area_ha,area_mu,study_ratio,coverage_ratio FROM analysis_statistics ORDER BY id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    names = ("inputClass", "overlayClass", "featureCount", "areaM2", "areaHa", "areaMu", "studyRatio", "coverageRatio")
    return {"datasetId": dataset["id"], "version": dataset["version"], "record": record,
            "rows": [dict(zip(names, row)) for row in rows], "total": total, "offset": offset, "limit": limit,
            "hasMore": offset + len(rows) < total}


def export_statistics(payload, work_dir, progress, cancelled):
    require_exact_keys(payload, {"dataset", "managedPath"})
    dataset, source = payload["dataset"], Path(payload["managedPath"])
    if _content_hash(source, cancelled) != dataset["version"]:
        _fail("Analysis snapshot changed", "dataset_changed")
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / "statistics.csv"
    names = ["analysisId", "inputClass", "inputClassIsNull", "overlayClass", "overlayClassIsNull", "featureCount", "areaM2", "areaHa", "areaMu", "studyRatio", "coverageRatio"]
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=names)
        writer.writeheader()
        offset = 0
        while True:
            _cancel(cancelled)
            page = result_page(dataset, source, {"offset": offset, "limit": 100})
            for row in page["rows"]:
                writer.writerow({"analysisId": page["record"]["id"], **row,
                                 "inputClassIsNull": str(row["inputClass"] is None).lower(),
                                 "overlayClassIsNull": str(row["overlayClass"] is None).lower()})
            offset += len(page["rows"])
            progress("exporting_statistics", offset, page["total"])
            if not page["hasMore"]:
                break
    if _content_hash(source, cancelled) != dataset["version"]:
        _fail("Analysis snapshot changed during export", "dataset_changed")
    return {"artifactPath": str(path.resolve())}
