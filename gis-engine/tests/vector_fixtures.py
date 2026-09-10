from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
from shapely.geometry import MultiPolygon, Point, Polygon, box


def ordinary_frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "code": ["001", "002", "003", "004"],
            "label": ["Chinese \u571f\u5730", "beta", "alpha", "alpha"],
            "large_id": pd.array([9007199254740993, None, 9007199254740995, 4], dtype="Int64"),
            "value": [4.5, None, 1.5, 1.5],
        },
        geometry=[
            box(500000, 3000000, 500100, 3000100),
            Polygon([(500200, 3000000), (500400, 3000000), (500400, 3000200), (500200, 3000200)],
                    holes=[[(500250, 3000050), (500350, 3000050), (500350, 3000150), (500250, 3000150)]]),
            MultiPolygon([box(500500, 3000000, 500550, 3000050), box(500600, 3000000, 500650, 3000050)]),
            box(500700, 3000000, 500750, 3000050),
        ],
        crs="EPSG:4547",
    )


def create_fixture_bundle(directory: Path) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    frame = ordinary_frame()
    gpkg = directory / "land.gpkg"
    pyogrio.write_dataframe(frame, gpkg, layer="land", driver="GPKG", use_arrow=True)
    pyogrio.write_dataframe(gpd.GeoDataFrame({"name": ["control"]}, geometry=[Point(500000, 3000000)], crs=frame.crs),
                            gpkg, layer="controls", driver="GPKG", use_arrow=True)
    shape = directory / "land.shp"
    pyogrio.write_dataframe(frame.drop(columns="large_id"), shape, driver="ESRI Shapefile", encoding="GBK", use_arrow=True)
    geojson = directory / "land.geojson"
    pyogrio.write_dataframe(frame.to_crs(4326), geojson, driver="GeoJSON", use_arrow=True)
    gdb = directory / "land.gdb"
    pyogrio.write_dataframe(frame, gdb, layer="land", driver="OpenFileGDB", use_arrow=True,
                            geometry_type="MultiPolygon", promote_to_multi=True,
                            layer_options={"TARGET_ARCGIS_VERSION": "ARCGIS_PRO_3_2_OR_LATER"})
    pyogrio.write_dataframe(gpd.GeoDataFrame({"name": ["control"]}, geometry=[Point(500000, 3000000)], crs=frame.crs),
                            gdb, layer="controls", driver="OpenFileGDB", use_arrow=True)
    return {"gpkg": str(gpkg), "shp": str(shape), "geojson": str(geojson), "gdb": str(gdb)}
