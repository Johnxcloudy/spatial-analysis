from __future__ import annotations

import json
import math
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pyogrio
import rasterio
from pyproj import Transformer
from rasterio.transform import Affine
from shapely.geometry import box, mapping

from .runtime import library_versions
from .resources import rasterio_environment
from .validation import require_exact_keys, require_path

DIAGNOSTIC_CRS = "EPSG:4547"


def _check(identifier: str, label: str, passed: bool, detail: str) -> dict:
    return {"id": identifier, "label": label, "passed": bool(passed), "detail": detail}


def _json_geometry(geometry) -> dict:
    return json.loads(json.dumps(mapping(geometry)))


def run_diagnostics(params: dict) -> dict:
    require_exact_keys(params, {"directory"})
    root = require_path(params["directory"], "directory")
    root.mkdir(parents=True, exist_ok=True)
    run_name = f"probe-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:8]}"
    artifact_dir = root / run_name
    artifact_dir.mkdir()
    started = time.perf_counter()

    source_geometry = box(500000.0, 3000000.0, 500100.0, 3000100.0)
    shifted_geometry = box(500050.0, 3000000.0, 500150.0, 3000100.0)
    source = gpd.GeoDataFrame({"name": ["source"]}, geometry=[source_geometry], crs=DIAGNOSTIC_CRS)
    shifted = gpd.GeoDataFrame({"name": ["overlay"]}, geometry=[shifted_geometry], crs=DIAGNOSTIC_CRS)
    intersection = gpd.overlay(source, shifted, how="intersection", keep_geom_type=True, make_valid=False)
    intersection["name"] = "intersection"
    measured_area = float(source.geometry.area.sum())
    intersection_area = float(intersection.geometry.area.sum())
    area_error = abs(measured_area - 10_000.0)
    checks = [
        _check("area", "Projected rectangle area", area_error <= 1e-6, f"Measured {measured_area:.9f} m2"),
        _check(
            "intersection",
            "Projected intersection area",
            abs(intersection_area - 5_000.0) <= 1e-6,
            f"Measured {intersection_area:.9f} m2",
        ),
    ]

    gpkg_path = (artifact_dir / "probe.gpkg").resolve()
    try:
        source.to_file(gpkg_path, layer="source", driver="GPKG", engine="pyogrio")
        intersection.to_file(gpkg_path, layer="intersection", driver="GPKG", engine="pyogrio", mode="a")
        source_read = gpd.read_file(gpkg_path, layer="source", engine="pyogrio")
        intersection_read = gpd.read_file(gpkg_path, layer="intersection", engine="pyogrio")
        gpkg_passed = (
            len(source_read) == 1
            and len(intersection_read) == 1
            and source_read.crs is not None
            and source_read.crs.equals(source.crs)
            and abs(float(source_read.geometry.area.sum()) - measured_area) <= 1e-6
            and abs(float(intersection_read.geometry.area.sum()) - intersection_area) <= 1e-6
        )
        gpkg_detail = "Wrote and reread source and intersection layers" if gpkg_passed else "Reread values did not match"
    except Exception as exc:
        gpkg_passed = False
        gpkg_detail = f"GeoPackage round trip failed: {type(exc).__name__}: {exc}"
    checks.append(_check("gpkg", "GeoPackage vector round trip", gpkg_passed, gpkg_detail))

    round_trip_error = -1.0
    try:
        forward = Transformer.from_crs(DIAGNOSTIC_CRS, "EPSG:4326", always_xy=True)
        inverse = Transformer.from_crs("EPSG:4326", DIAGNOSTIC_CRS, always_xy=True)
        longitude, latitude = forward.transform(500050.0, 3000050.0)
        restored_x, restored_y = inverse.transform(longitude, latitude)
        round_trip_error = math.hypot(restored_x - 500050.0, restored_y - 3000050.0)
        proj_passed = math.isfinite(round_trip_error) and round_trip_error <= 1e-6
        proj_detail = f"Forward/inverse error {round_trip_error:.12f} m"
    except Exception as exc:
        proj_passed = False
        proj_detail = f"PROJ transformation failed: {type(exc).__name__}: {exc}"
    checks.append(_check("proj", "PROJ forward/inverse transformation", proj_passed, proj_detail))

    vector_drivers = pyogrio.list_drivers()
    openfilegdb_mode = vector_drivers.get("OpenFileGDB")
    checks.append(
        _check(
            "openfilegdb",
            "OpenFileGDB driver availability",
            openfilegdb_mode is not None,
            f"Available with capability {openfilegdb_mode}" if openfilegdb_mode else "Driver is unavailable in this runtime",
        )
    )

    geotiff_path = artifact_dir / "probe.tif"
    try:
        with rasterio_environment() as environment:
            gtiff_available = "GTiff" in environment.drivers()
            if not gtiff_available:
                raise RuntimeError("GTiff driver is unavailable")
            values = np.array([[1, 2], [3, 4]], dtype=np.uint16)
            transform = Affine(50.0, 0.0, 500000.0, 0.0, -50.0, 3000100.0)
            with rasterio.open(
                geotiff_path,
                "w",
                driver="GTiff",
                width=2,
                height=2,
                count=1,
                dtype=values.dtype,
                crs=DIAGNOSTIC_CRS,
                transform=transform,
                nodata=0,
            ) as dataset:
                dataset.write(values, 1)
            with rasterio.open(geotiff_path) as dataset:
                raster_values = dataset.read(1)
                geotiff_passed = (
                    dataset.driver == "GTiff"
                    and dataset.crs is not None
                    and dataset.crs.to_epsg() == 4547
                    and dataset.nodata == 0
                    and dataset.transform == transform
                    and np.array_equal(raster_values, values)
                )
        geotiff_detail = "Wrote and reread a 2x2 projected UInt16 raster" if geotiff_passed else "Raster metadata or pixels changed"
    except Exception as exc:
        geotiff_passed = False
        geotiff_detail = f"GeoTIFF round trip failed: {type(exc).__name__}: {exc}"
    checks.append(_check("geotiff", "GeoTIFF raster round trip", geotiff_passed, geotiff_detail))

    try:
        preview_source = source.to_crs("EPSG:4326").geometry.iloc[0]
        preview_intersection = intersection.to_crs("EPSG:4326").geometry.iloc[0]
        preview = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": "100 m x 100 m source", "role": "source"},
                    "geometry": _json_geometry(preview_source),
                },
                {
                    "type": "Feature",
                    "properties": {"name": "50 m x 100 m intersection", "role": "intersection"},
                    "geometry": _json_geometry(preview_intersection),
                },
            ],
        }
    except Exception as exc:
        preview = {"type": "FeatureCollection", "features": []}
        checks.append(_check("preview", "WGS84 diagnostic preview", False, f"Preview transformation failed: {type(exc).__name__}: {exc}"))

    report_path = (artifact_dir / "report.json").resolve()
    report = {
        "ok": all(check["passed"] for check in checks),
        "crs": DIAGNOSTIC_CRS,
        "expectedAreaM2": 10_000.0,
        "measuredAreaM2": measured_area,
        "expectedIntersectionAreaM2": 5_000.0,
        "intersectionAreaM2": intersection_area,
        "areaErrorM2": area_error,
        "roundTripErrorM": round_trip_error,
        "checks": checks,
        "preview": preview,
        "sourceBounds": [float(value) for value in source.total_bounds],
        "versions": library_versions(),
        "reportPath": str(report_path),
        "geopackagePath": str(gpkg_path),
        "durationMs": int((time.perf_counter() - started) * 1000),
    }
    temporary_report = report_path.with_suffix(".json.tmp")
    temporary_report.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary_report.replace(report_path)
    return report
