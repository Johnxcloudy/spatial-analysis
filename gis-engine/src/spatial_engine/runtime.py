from __future__ import annotations

import platform
import sys
from importlib import metadata

import geopandas
import openpyxl
import pyarrow
import pyogrio
import pyproj
import rasterio
import shapely
from rasterio.io import MemoryFile
from rasterio.transform import Affine

from . import __version__
from .logging_config import log_path
from .resources import rasterio_environment


def library_versions() -> dict[str, str]:
    return {
        "geopandas": geopandas.__version__,
        "shapely": shapely.__version__,
        "pyogrio": pyogrio.__version__,
        "pyproj": pyproj.__version__,
        "rasterio": rasterio.__version__,
        "gdal": pyogrio.__gdal_version_string__,
        "proj": pyproj.proj_version_str,
        "numpy": metadata.version("numpy"),
        "pyarrow": pyarrow.__version__,
        "openpyxl": openpyxl.__version__,
    }


def driver_capabilities() -> dict[str, str]:
    capabilities = dict(pyogrio.list_drivers())
    with rasterio_environment() as environment:
        raster_drivers = environment.drivers()
        if "GTiff" not in raster_drivers:
            return dict(sorted((name, str(mode)) for name, mode in capabilities.items()))
        try:
            with MemoryFile() as memory:
                with memory.open(
                    driver="GTiff",
                    width=1,
                    height=1,
                    count=1,
                    dtype="uint8",
                    crs="EPSG:4326",
                    transform=Affine(1.0, 0.0, 100.0, 0.0, -1.0, 30.0),
                ):
                    pass
            capabilities["GTiff"] = "rw"
        except Exception:
            capabilities["GTiff"] = "r"
    return dict(sorted((name, str(mode)) for name, mode in capabilities.items()))


def runtime_info() -> dict:
    return {
        "protocolVersion": 3,
        "engineVersion": __version__,
        "pythonVersion": platform.python_version(),
        "packaged": bool(getattr(sys, "frozen", False)),
        "versions": library_versions(),
        "drivers": driver_capabilities(),
        "logPath": str(log_path()),
    }
