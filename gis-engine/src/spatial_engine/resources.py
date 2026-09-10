from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def resource_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root).resolve()
    return Path(__file__).resolve().parent


def resource_path(relative_path: str) -> Path:
    return resource_root() / relative_path


def _first_directory(*candidates: Path) -> Path | None:
    return next((path for path in candidates if path.is_dir()), None)


def rasterio_env_options() -> dict[str, str]:
    root = resource_root()
    gdal_data = _first_directory(root / "rasterio" / "gdal_data")
    proj_data = _first_directory(root / "rasterio" / "proj_data")
    options: dict[str, str] = {}
    if gdal_data:
        options["GDAL_DATA"] = str(gdal_data)
    if proj_data:
        options["PROJ_DATA"] = str(proj_data)
    return options


@contextmanager
def rasterio_environment(**extra_options: object) -> Iterator[object]:
    import rasterio
    from rasterio.env import set_proj_data_search_path

    options = {**extra_options, **rasterio_env_options()}
    with rasterio.Env(**options) as environment:
        proj_data = options.get("PROJ_DATA")
        if proj_data:
            # GDALEnv.start() resets the PROJ search path during Env entry.
            set_proj_data_search_path(proj_data)
        yield environment


def configure_native_data_paths() -> None:
    """Configure each native projection library with its bundled data."""
    if not getattr(sys, "frozen", False):
        return
    root = resource_root()
    pyproj_data = _first_directory(root / "pyproj" / "proj_dir" / "share" / "proj")
    if pyproj_data:
        from pyproj import datadir

        datadir.set_data_dir(str(pyproj_data))
    rasterio_proj_data = _first_directory(root / "rasterio" / "proj_data")
    if rasterio_proj_data:
        from rasterio.env import set_proj_data_search_path

        set_proj_data_search_path(str(rasterio_proj_data))
