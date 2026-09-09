from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import uuid
import warnings
from collections import OrderedDict
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from rasterio.enums import ColorInterp, Resampling
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window

from .errors import DomainError, InvalidParamsError
from .resources import rasterio_environment
from .validation import require_exact_keys, require_object, require_path, require_string
from .vectors import _cancel, wgs84_bounds

MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_DECODED_BYTES = 2 * 1024 * 1024 * 1024
MAX_BLOCK_BYTES = 16 * 1024 * 1024
MAX_DIMENSION = 100_000
MAX_BANDS = 16
MAX_METADATA_BYTES = 256 * 1024
MAX_RENDER_DIMENSION = 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
SAMPLE_DIMENSION = 256
MERCATOR_LIMIT = 20037508.342789244
GDAL_MEMORY_BYTES = 64 * 1024 * 1024
REAL_DTYPES = {"uint8", "int8", "uint16", "int16", "uint32", "int32", "uint64", "int64", "float32", "float64"}
_INTEGRITY_CACHE: OrderedDict[tuple, tuple] = OrderedDict()
MAX_INTEGRITY_CACHE = 128


def _bounded(value: dict, limit: int, kind: str) -> dict:
    if len(json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")) > limit:
        raise DomainError("Raster metadata or response exceeds the resource limit", kind=kind)
    return value


def _source_path(value) -> Path:
    path = require_path(value, "sourcePath")
    if path.suffix.lower() not in {".tif", ".tiff"} or not path.is_file():
        raise DomainError("Source must be an existing GeoTIFF file", kind="unsupported_source")
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise DomainError("Raster source exceeds the 512 MiB file limit", kind="source_limit")
    return path


def _sidecars(path: Path, files=()) -> None:
    candidates = {path.with_suffix(suffix) for suffix in (".tfw", ".tifw", ".tiffw", ".wld", ".tab", ".prj", ".aux", ".aux.xml", ".rrd", ".ovr", ".msk")}
    candidates.update(Path(str(path) + suffix) for suffix in (".aux.xml", ".msk", ".ovr", ".aux", ".xml"))
    external = {str(item) for item in candidates if item.is_file()}
    external.update(str(item) for raw in files if (item := Path(raw).resolve()) != path.resolve())
    if external:
        raise DomainError("Only self-contained GeoTIFF files are supported; external sidecars must first be internalized", kind="external_raster_dependencies", detail="; ".join(sorted(external)))


@contextmanager
def _open(path: Path, *, base_resolution: bool = False):
    _sidecars(path)
    try:
        with rasterio_environment(GDAL_CACHEMAX=GDAL_MEMORY_BYTES, GDAL_PAM_ENABLED="NO", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", rasterio.errors.NotGeoreferencedWarning)
                source = rasterio.open(path, sharing=False, **({"OVERVIEW_LEVEL": "NONE"} if base_resolution else {}))
            with source:
                if any(issubclass(item.category, rasterio.errors.NotGeoreferencedWarning) for item in caught):
                    raise DomainError("GeoTIFF requires an explicit affine geotransform; missing georeferencing cannot be inferred", kind="unsupported_georeferencing")
                if source.driver != "GTiff":
                    raise DomainError("Raster contents do not match GeoTIFF", kind="unsupported_source")
                _sidecars(path, source.files)
                _validate_source(source)
                yield source
    except (DomainError, InvalidParamsError):
        raise
    except Exception as exc:
        raise DomainError("Could not read GeoTIFF data", kind="raster_read_failed", detail=str(exc)) from exc


def _validate_source(source) -> None:
    if not 1 <= source.count <= MAX_BANDS or not 1 <= source.width <= MAX_DIMENSION or not 1 <= source.height <= MAX_DIMENSION:
        raise DomainError("Raster dimensions or band count exceed the resource limit", kind="source_limit")
    if any(dtype not in REAL_DTYPES for dtype in source.dtypes):
        raise DomainError("Only real-valued raster bands are supported", kind="unsupported_raster_type")
    if source.subdatasets:
        raise DomainError("Multi-image TIFF subdatasets are not supported in this milestone", kind="unsupported_raster_structure")
    if any(dtype in {"int64", "uint64"} and nodata is not None and abs(nodata) > 9007199254740991
           for dtype, nodata in zip(source.dtypes, source.nodatavals)):
        raise DomainError("64-bit integer NoData outside the exact numeric metadata range cannot be interpreted reliably", kind="unsupported_raster_nodata")
    decoded = source.width * source.height * sum(np.dtype(dtype).itemsize for dtype in source.dtypes)
    if decoded > MAX_DECODED_BYTES:
        raise DomainError("Raster decoded size exceeds the 2 GiB limit", kind="source_limit")
    if any(height * width * np.dtype(dtype).itemsize * source.count > MAX_BLOCK_BYTES for (height, width), dtype in zip(source.block_shapes, source.dtypes)):
        raise DomainError("Raster block exceeds the 16 MiB decoding limit", kind="source_limit")
    transform = source.transform
    if not all(math.isfinite(value) for value in transform[:6]) or not math.isfinite(transform.determinant) or transform.determinant == 0:
        raise DomainError("Raster affine transform must be finite and invertible", kind="unsupported_georeferencing")
    if source.gcps[0] or source.rpcs is not None or source.tags(ns="GEOLOCATION"):
        raise DomainError("GCP, RPC and geolocation-array rasters are not supported in this milestone", kind="unsupported_georeferencing")
    if not all(math.isfinite(value) for value in (*source.scales, *source.offsets)):
        raise DomainError("Raster scale and offset must be finite", kind="invalid_raster_metadata")


def _number(value):
    if value is None:
        return None
    value = float(value)
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    return value


def _valid(data, masks, nodata):
    valid = (masks > 0) & np.isfinite(data)
    if nodata is not None:
        valid &= ~np.isnan(data) if math.isnan(nodata) else data != nodata
    return valid


def _metadata(source) -> tuple[dict, dict]:
    crs = CRS.from_user_input(source.crs) if source.crs else None
    authority = crs.to_authority() if crs else None
    crs_wkt = crs.to_wkt() if crs else None
    vertical = None
    if crs:
        vertical = next((part.to_wkt() for part in crs.sub_crs_list if part.is_vertical), None)
        if crs.is_vertical:
            vertical = crs_wkt
    bounds = [float(value) for value in source.bounds]
    if not all(math.isfinite(value) for value in bounds):
        raise DomainError("Raster bounds must be finite", kind="invalid_raster_metadata")
    horizontal_unit = crs.axis_info[0].unit_name if crs and len(crs.axis_info) >= 2 and not crs.is_vertical else None
    width, height = min(source.width, SAMPLE_DIMENSION), min(source.height, SAMPLE_DIMENSION)
    bands = []
    # Nearest reads must not silently sample pre-existing average-resampled overviews.
    has_overviews = any(source.overviews(index) for index in source.indexes)
    with rasterio.open(source.name, OVERVIEW_LEVEL="NONE") if has_overviews else nullcontext(source) as sampled:
        for index in source.indexes:
            data = sampled.read(index, out_shape=(height, width), resampling=Resampling.nearest)
            masks = sampled.read_masks(index, out_shape=(height, width), resampling=Resampling.nearest)
            valid = _valid(data, masks, source.nodatavals[index - 1])
            values = data[valid]
            bands.append({"index": index, "dtype": source.dtypes[index - 1], "description": source.descriptions[index - 1],
                          "unit": source.units[index - 1], "scale": float(source.scales[index - 1]), "offset": float(source.offsets[index - 1]),
                          "noData": _number(source.nodatavals[index - 1]), "colorInterpretation": source.colorinterp[index - 1].name,
                          "maskFlags": [flag.name for flag in source.mask_flag_enums[index - 1]], "overviews": source.overviews(index),
                          "tags": source.tags(index), "sampleMin": float(values.min()) if len(values) else None,
                          "sampleMax": float(values.max()) if len(values) else None, "sampledPixels": int(data.size),
                          "validSamplePixels": int(len(values))})
    # GDAL's derived-subdataset domain contains generated source paths, not stored TIFF metadata.
    namespaces = {name: source.tags(ns=name) for name in source.tag_namespaces() if name != "DERIVED_SUBDATASETS"}
    raster = {"width": source.width, "height": source.height, "bandCount": source.count,
              "transform": list(source.transform[:6]), "resolution": [math.hypot(source.transform.a, source.transform.d), math.hypot(source.transform.b, source.transform.e)],
              "bands": bands, "horizontalUnit": horizontal_unit, "verticalCrsWkt": vertical,
              "tags": source.tags(), "tagNamespaces": namespaces}
    spatial = {"crsWkt": crs_wkt, "crsAuthority": ":".join(authority) if authority else None,
               "bounds": bounds, "boundsWgs84": wgs84_bounds(bounds, crs_wkt)}
    _bounded({"raster": raster, **spatial}, MAX_METADATA_BYTES, "source_limit")
    return raster, spatial


def inspect_raster(params: dict) -> dict:
    require_exact_keys(params, {"sourcePath"})
    path = _source_path(params["sourcePath"])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with _open(path) as source:
            raster, spatial = _metadata(source)
    messages = [str(item.message) for item in caught]
    messages.append("Band sampleMin/sampleMax use at most a 256 by 256 nearest-neighbor sample grid; they are not full-raster statistics.")
    if spatial["crsWkt"] is None:
        messages.append("Source CRS is unknown; map display and map-coordinate pixel queries are unavailable.")
    if any(band["validSamplePixels"] == 0 for band in raster["bands"]):
        messages.append("A band has no finite valid sampled values; its default display range is [0, 1].")
    if any(band["dtype"] in {"int64", "uint64"} for band in raster["bands"]):
        messages.append("64-bit integers are retained in the TIFF and raw pixel strings; display ranges use floating-point approximations.")
    if any(band["colorInterpretation"] == "palette" for band in raster["bands"]):
        messages.append("Source palette colors are preserved in the TIFF; this milestone displays explicit grayscale or RGB numeric ranges.")
    return _bounded({"sourcePath": str(path), "driver": "GTiff", **spatial, "raster": raster, "warnings": messages}, MAX_METADATA_BYTES, "source_limit")


def _scan(source, *, progress, cancelled) -> int:
    total = sum(math.ceil(source.height / shape[0]) * math.ceil(source.width / shape[1]) for shape in source.block_shapes)
    completed = 0
    for index in source.indexes:
        for _, window in source.block_windows(index):
            _cancel(cancelled)
            source.read(index, window=window)
            source.read_masks(index, window=window)
            completed += 1
            progress("validating", completed, total)
    return completed


def _copy(source: Path, target: Path, *, progress, cancelled) -> str:
    digest = hashlib.sha256()
    total, completed = source.stat().st_size, 0
    with source.open("rb") as reader, target.open("xb") as writer:
        while chunk := reader.read(1024 * 1024):
            _cancel(cancelled)
            writer.write(chunk)
            digest.update(chunk)
            completed += len(chunk)
            if completed > MAX_SOURCE_BYTES:
                raise DomainError("Raster grew beyond the 512 MiB limit while copying", kind="source_limit")
            progress("copying", completed, total)
        writer.flush()
        os.fsync(writer.fileno())
    return digest.hexdigest()


def _content_hash(path: Path, cancelled=lambda: False) -> str:
    digest, completed = hashlib.sha256(), 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            _cancel(cancelled)
            completed += len(chunk)
            if completed > MAX_SOURCE_BYTES:
                raise DomainError("Raster exceeds the 512 MiB limit while hashing", kind="source_limit")
            digest.update(chunk)
    return digest.hexdigest()


def import_raster(payload: dict, work_dir: Path, *, progress, cancelled) -> dict:
    require_exact_keys(payload, {"sourcePath", "datasetId"})
    path = _source_path(payload["sourcePath"])
    dataset_id = require_string(payload["datasetId"], "datasetId", maximum=64)
    try:
        uuid.UUID(dataset_id)
    except ValueError as exc:
        raise InvalidParamsError("datasetId must be a UUID") from exc
    _cancel(cancelled)
    progress("fingerprinting", None, None)
    before = _content_hash(path, cancelled)
    inspection = inspect_raster({"sourcePath": str(path)})
    with _open(path) as source:
        blocks = _scan(source, progress=progress, cancelled=cancelled)
    work_dir.mkdir(parents=True, exist_ok=True)
    artifact = (work_dir / "snapshot.tif").resolve()
    if artifact.exists():
        raise DomainError("Snapshot destination already exists", kind="destination_exists")
    copied = _copy(path, artifact, progress=progress, cancelled=cancelled)
    if before != copied or _content_hash(path, cancelled) != before:
        raise DomainError("Source changed during raster import", kind="source_changed")
    _sidecars(path)
    spatial = {key: inspection[key] for key in ("crsWkt", "crsAuthority", "bounds", "boundsWgs84")}
    dataset = {"id": dataset_id, "version": copied, "name": path.stem, "kind": "raster",
               "source": {"path": str(path), "layer": "raster", "driver": "GTiff", "fingerprint": before, "encoding": None,
                          "assignedCrs": None, "crsWkt": inspection["crsWkt"], "metadata": {"snapshotPolicy": "byte-for-byte self-contained GeoTIFF"}},
               "relativePath": f"rasters/{dataset_id}.tif", **spatial, "raster": inspection["raster"],
               "report": {"status": "restricted" if spatial["crsWkt"] is None or spatial["boundsWgs84"] is None else "warning",
                          "checks": [{"code": "all_blocks_readable", "passed": True, "detail": "Every source band block and mask was decoded without repair", "count": blocks},
                                     {"code": "source_unchanged", "passed": True, "detail": "Source SHA-256 before and after copying matches snapshot bytes"},
                                     {"code": "crs_known", "passed": spatial["crsWkt"] is not None, "detail": "Only the original source CRS is used"}],
                          "warnings": inspection["warnings"], "notChecked": ["Positional and radiometric accuracy", "Full-raster statistics", "Vertical datum and elevation units when not explicitly recorded"],
                          "counts": {"pixels": inspection["raster"]["width"] * inspection["raster"]["height"], "bands": inspection["raster"]["bandCount"]},
                          "validatorVersion": "1"}, "createdAt": datetime.now(UTC).isoformat()}
    verify_snapshot(artifact, dataset, cancelled)
    dataset["report"]["checks"].append({"code": "roundtrip", "passed": True, "detail": "Snapshot bytes, raster metadata, source CRS and bounds were reread and compared"})
    _bounded(dataset, MAX_METADATA_BYTES, "source_limit")
    return {"dataset": dataset, "artifactPath": str(artifact)}


def verify_snapshot(path: Path, dataset: dict, cancelled=lambda: False) -> None:
    _cancel(cancelled)
    if dataset.get("kind") != "raster":
        raise DomainError("Dataset is not a raster", kind="dataset_not_raster")
    if not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES or _content_hash(path, cancelled) != dataset["version"]:
        raise DomainError("Managed raster snapshot changed after import", kind="snapshot_changed")
    with _open(path) as source:
        raster, spatial = _metadata(source)
    if raster != dataset["raster"] or any(spatial[key] != dataset[key] for key in spatial):
        raise DomainError("Snapshot raster metadata does not match registered metadata", kind="roundtrip_failed")


def export_raster(payload: dict, work_dir: Path, *, progress, cancelled) -> dict:
    dataset, path = payload["dataset"], require_path(payload["managedPath"], "managedPath")
    progress("validating", None, None)
    verify_snapshot(path, dataset, cancelled)
    work_dir.mkdir(parents=True, exist_ok=True)
    artifact = (work_dir / "export.tif").resolve()
    if artifact.exists():
        raise DomainError("Export destination already exists", kind="destination_exists")
    copied = _copy(path, artifact, progress=progress, cancelled=cancelled)
    if copied != dataset["version"] or _content_hash(path, cancelled) != copied:
        raise DomainError("Snapshot changed during raster export", kind="source_changed")
    verify_snapshot(artifact, dataset, cancelled)
    return {"artifactPath": str(artifact)}


def _finite(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidParamsError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise InvalidParamsError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise InvalidParamsError(f"{name} must be a finite number")
    return result


def _integer(value, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise InvalidParamsError(f"{name} must be an integer from 1 to {maximum}")
    return value


def validate_style(style, dataset: dict) -> dict:
    style = require_object(style, "style")
    require_exact_keys(style, {"mode", "bands", "ranges", "resampling"})
    mode = style["mode"]
    if mode not in ("gray", "rgb") or style["resampling"] != "nearest":
        raise InvalidParamsError("Raster style requires gray or rgb mode and nearest resampling")
    length = 1 if mode == "gray" else 3
    bands, ranges = style["bands"], style["ranges"]
    if not isinstance(bands, list) or len(bands) != length or not isinstance(ranges, list) or len(ranges) != length:
        raise InvalidParamsError("Raster style band and range counts must match its mode")
    checked_bands = [_integer(band, "band", dataset["raster"]["bandCount"]) for band in bands]
    checked_ranges = []
    for pair in ranges:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise InvalidParamsError("Each raster range must contain a minimum and maximum")
        low, high = [_finite(value, "range") for value in pair]
        if low > high:
            raise InvalidParamsError("Raster range minimum must not exceed maximum")
        checked_ranges.append([low, high])
    return {"mode": mode, "bands": checked_bands, "ranges": checked_ranges, "resampling": "nearest"}


def default_style(dataset: dict) -> dict:
    bands = dataset["raster"]["bands"]
    colors = {band["colorInterpretation"]: band["index"] for band in bands}
    rgb = all(color in colors for color in ("red", "green", "blue"))
    selected = [colors[color] for color in ("red", "green", "blue")] if rgb else [1]
    ranges = [[bands[index - 1]["sampleMin"], bands[index - 1]["sampleMax"]] if bands[index - 1]["validSamplePixels"] else [0, 1] for index in selected]
    return {"mode": "rgb" if rgb else "gray", "bands": selected, "ranges": ranges, "resampling": "nearest"}


def _map_crs(dataset: dict) -> str:
    if dataset.get("kind") != "raster":
        raise DomainError("Dataset is not a raster", kind="dataset_not_raster")
    if not dataset.get("crsWkt"):
        raise DomainError("Source CRS is unknown; map display and map-coordinate queries are unavailable", kind="crs_required")
    crs = CRS.from_user_input(dataset["crsWkt"])
    if crs.is_vertical or not (crs.is_projected or crs.is_geographic or crs.is_compound):
        raise DomainError("Raster CRS has no supported horizontal coordinate system", kind="unsupported_crs")
    return dataset["crsWkt"]


def _stat_signature(path: Path) -> tuple:
    try:
        stat = path.stat()
    except OSError as exc:
        raise DomainError("Managed raster snapshot is unavailable", kind="snapshot_changed", detail=str(exc)) from exc
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_dev, stat.st_ino


def _managed_integrity(path: Path, dataset: dict) -> tuple:
    _sidecars(path)
    signature = _stat_signature(path)
    expected = {key: dataset[key] for key in ("raster", "crsWkt", "crsAuthority", "bounds", "boundsWgs84")}
    metadata_hash = hashlib.sha256(json.dumps(expected, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()
    key = (str(path.resolve()), dataset["version"], metadata_hash)
    if _INTEGRITY_CACHE.get(key) != signature:
        verify_snapshot(path, dataset)
        if _stat_signature(path) != signature:
            raise DomainError("Managed raster changed during integrity verification", kind="snapshot_changed")
        _INTEGRITY_CACHE[key] = signature
    _INTEGRITY_CACHE.move_to_end(key)
    while len(_INTEGRITY_CACHE) > MAX_INTEGRITY_CACHE:
        _INTEGRITY_CACHE.popitem(last=False)
    return signature


def _unchanged(path: Path, signature: tuple) -> None:
    _sidecars(path)
    if _stat_signature(path) != signature:
        raise DomainError("Managed raster changed during query", kind="snapshot_changed")


def _render_params(params: dict, dataset: dict) -> tuple[list, int, int, dict]:
    require_exact_keys(params, {"bbox", "width", "height", "style"} | (params.keys() & {"path", "datasetId"}))
    value = params["bbox"]
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise InvalidParamsError("bbox must contain four EPSG:3857 coordinates")
    bbox = [_finite(number, "bbox coordinate") for number in value]
    if not (-MERCATOR_LIMIT <= bbox[0] < bbox[2] <= MERCATOR_LIMIT and -MERCATOR_LIMIT <= bbox[1] < bbox[3] <= MERCATOR_LIMIT):
        raise InvalidParamsError("bbox must be ordered and within the EPSG:3857 world extent")
    return bbox, _integer(params["width"], "width", MAX_RENDER_DIMENSION), _integer(params["height"], "height", MAX_RENDER_DIMENSION), validate_style(params["style"], dataset)


def render(dataset: dict, managed_path: Path, params: dict) -> dict:
    _map_crs(dataset)
    bbox, width, height, style = _render_params(params, dataset)
    signature = _managed_integrity(managed_path, dataset)
    rgba = np.zeros((4, height, width), dtype=np.uint8)
    with _open(managed_path, base_resolution=True) as source:
        has_alpha = ColorInterp.alpha in source.colorinterp
        # Let GDAL honor internal masks; explicit NoData is excluded separately below.
        # Supplying source NoData to the warper otherwise overrides an internal mask.
        with WarpedVRT(source, crs="EPSG:3857", transform=from_bounds(*bbox, width, height), width=width, height=height,
                       src_nodata=None, resampling=Resampling.nearest, add_alpha=not has_alpha, warp_mem_limit=64, dtype="float64") as warped:
            data = warped.read(style["bands"], out_dtype="float64")
            masks = warped.read_masks(style["bands"])
            alpha = masks.min(axis=0)
            alpha_index = next((index for index, color in enumerate(warped.colorinterp, 1) if color == ColorInterp.alpha), None)
            if alpha_index:
                coverage = warped.read(alpha_index)
                maximum = 255
                if has_alpha and source.dtypes[alpha_index - 1] == "uint16":
                    bits = source.tags(alpha_index, ns="IMAGE_STRUCTURE").get("NBITS", "16")
                    maximum = 2 ** int(bits) - 1
                # An existing alpha band retains its source range even when the VRT is float64.
                coverage = np.rint(np.clip(coverage / maximum, 0, 1) * 255).astype(np.uint8)
                alpha = np.minimum(alpha, coverage)
            finite = np.isfinite(data).all(axis=0)
            for channel, band in enumerate(style["bands"]):
                nodata = source.nodatavals[band - 1]
                if nodata is not None:
                    finite &= ~np.isnan(data[channel]) if math.isnan(nodata) else data[channel] != nodata
            alpha[~finite] = 0
            for channel, (low, high) in enumerate(style["ranges"]):
                if low == high:
                    scaled = np.full((height, width), 127, dtype=np.uint8)
                else:
                    # Dividing each term first prevents overflow for opposite large finite endpoints.
                    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                        values = (data[channel] / 2 - low / 2) / (high / 2 - low / 2)
                        scaled = np.nan_to_num(np.clip(values, 0, 1), nan=0, posinf=1, neginf=0)
                        scaled = np.rint(scaled * 255).astype(np.uint8)
                rgba[channel] = scaled
            if style["mode"] == "gray":
                rgba[1], rgba[2] = rgba[0], rgba[0]
            rgba[3] = alpha
    _unchanged(managed_path, signature)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", rasterio.errors.NotGeoreferencedWarning)
        with MemoryFile() as memory:
            with memory.open(driver="PNG", width=width, height=height, count=4, dtype="uint8") as image:
                image.write(rgba)
            encoded = base64.b64encode(memory.read()).decode("ascii")
    return _bounded({"datasetId": dataset["id"], "version": dataset["version"], "bbox": bbox, "dataCrs": "EPSG:3857",
                     "width": width, "height": height, "mimeType": "image/png", "imageBase64": encoded, "resampling": "nearest"}, MAX_RESPONSE_BYTES, "query_limit")


def _raw_string(value) -> str:
    if np.issubdtype(type(value), np.integer):
        return str(int(value))
    value = float(value)
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    return repr(value)


def sample(dataset: dict, managed_path: Path, params: dict) -> dict:
    crs_wkt = _map_crs(dataset)
    require_exact_keys(params, {"coordinate"} | (params.keys() & {"path", "datasetId"}))
    coordinate = params["coordinate"]
    if not isinstance(coordinate, (list, tuple)) or len(coordinate) != 2:
        raise InvalidParamsError("coordinate must contain WGS84 longitude and latitude")
    coordinate = [_finite(value, "coordinate") for value in coordinate]
    if not -180 <= coordinate[0] <= 180 or not -90 <= coordinate[1] <= 90:
        raise InvalidParamsError("coordinate must be within WGS84 longitude/latitude bounds")
    signature = _managed_integrity(managed_path, dataset)
    try:
        xy = Transformer.from_crs(4326, crs_wkt, always_xy=True).transform(*coordinate, errcheck=True)
        if not all(math.isfinite(value) for value in xy):
            raise ValueError("non-finite source coordinates")
    except Exception as exc:
        raise DomainError("Map coordinate cannot be transformed into the raster CRS", kind="display_transform_failed", detail=str(exc)) from exc
    with _open(managed_path) as source:
        row, column = source.index(*xy)
        inside = 0 <= row < source.height and 0 <= column < source.width
        bands = []
        for index in source.indexes:
            if not inside:
                bands.append({"index": index, "rawValue": None, "value": None, "valid": False, "reason": "outside"})
                continue
            window = Window(column, row, 1, 1)
            raw = source.read(index, window=window)[0, 0]
            mask = source.read_masks(index, window=window)[0, 0]
            reason, value = None, None
            if not np.isfinite(raw):
                reason = "non_finite"
            elif not _valid(raw, mask, source.nodatavals[index - 1]):
                reason = "nodata_or_masked"
            elif np.issubdtype(type(raw), np.integer) and abs(int(raw)) > 9007199254740991:
                reason = "scaled_value_precision_unavailable"
            else:
                value = float(raw) * source.scales[index - 1] + source.offsets[index - 1]
                if not math.isfinite(value):
                    value, reason = None, "scaled_value_non_finite"
            bands.append({"index": index, "rawValue": _raw_string(raw), "value": value,
                          "valid": bool(_valid(raw, mask, source.nodatavals[index - 1])), "reason": reason})
    _unchanged(managed_path, signature)
    return {"datasetId": dataset["id"], "version": dataset["version"], "coordinate": coordinate,
            "sourceCoordinate": list(xy), "pixel": {"row": int(row), "column": int(column)} if inside else None,
            "inside": inside, "bands": bands}
