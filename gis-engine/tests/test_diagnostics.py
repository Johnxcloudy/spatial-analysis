from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd

from spatial_engine.rpc import Engine


def test_diagnostics_run_performs_real_gis_round_trips(tmp_path: Path) -> None:
    result = Engine().dispatch("diagnostics.run", {"directory": str(tmp_path / "diagnostics")})

    assert result["ok"] is True
    assert result["crs"] == "EPSG:4547"
    assert result["expectedAreaM2"] == 10_000.0
    assert result["measuredAreaM2"] == 10_000.0
    assert result["expectedIntersectionAreaM2"] == 5_000.0
    assert result["intersectionAreaM2"] == 5_000.0
    assert result["areaErrorM2"] <= 1e-6
    assert result["roundTripErrorM"] <= 1e-6
    assert result["sourceBounds"] == [500000.0, 3000000.0, 500100.0, 3000100.0]
    assert result["durationMs"] >= 0
    assert result["preview"]["type"] == "FeatureCollection"
    assert [feature["properties"]["role"] for feature in result["preview"]["features"]] == ["source", "intersection"]

    checks = {check["id"]: check for check in result["checks"]}
    assert checks.keys() >= {"area", "intersection", "gpkg", "proj", "openfilegdb", "geotiff"}
    assert all(isinstance(check["passed"], bool) and check["detail"] for check in checks.values())
    assert checks["area"]["passed"] and checks["intersection"]["passed"]
    assert checks["gpkg"]["passed"] and checks["proj"]["passed"] and checks["geotiff"]["passed"]

    report_path = Path(result["reportPath"])
    gpkg_path = Path(result["geopackagePath"])
    assert report_path.parent == gpkg_path.parent
    assert report_path.parent.parent == tmp_path / "diagnostics"
    assert json.loads(report_path.read_text(encoding="utf-8"))["reportPath"] == str(report_path.resolve())
    assert set(gpd.list_layers(gpkg_path)["name"]) == {"source", "intersection"}
    assert len(gpd.read_file(gpkg_path, layer="source", engine="pyogrio")) == 1


def test_diagnostics_uses_unique_artifact_directory(tmp_path: Path) -> None:
    engine = Engine()
    first = engine.dispatch("diagnostics.run", {"directory": str(tmp_path)})
    second = engine.dispatch("diagnostics.run", {"directory": str(tmp_path)})

    assert Path(first["reportPath"]).parent != Path(second["reportPath"]).parent
