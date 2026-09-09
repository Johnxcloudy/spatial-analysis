from __future__ import annotations

from pathlib import Path

import pyproj.datadir
import rasterio
import rasterio.env
from rasterio.crs import CRS

import spatial_engine.resources as resources


def test_frozen_rasterio_paths_are_scoped_to_rasterio_bundle(tmp_path: Path, monkeypatch) -> None:
    rasterio_root = tmp_path / "rasterio"
    (rasterio_root / "gdal_data").mkdir(parents=True)
    (rasterio_root / "proj_data").mkdir()
    (tmp_path / "pyproj" / "proj_dir" / "share" / "proj").mkdir(parents=True)
    monkeypatch.setattr(resources, "resource_root", lambda: tmp_path)

    options = resources.rasterio_env_options()

    assert options == {
        "GDAL_DATA": str(rasterio_root / "gdal_data"),
        "PROJ_DATA": str(rasterio_root / "proj_data"),
    }


def test_frozen_startup_sets_each_librarys_own_proj_search_path(tmp_path: Path, monkeypatch) -> None:
    pyproj_root = tmp_path / "pyproj" / "proj_dir" / "share" / "proj"
    rasterio_root = tmp_path / "rasterio" / "proj_data"
    pyproj_root.mkdir(parents=True)
    rasterio_root.mkdir(parents=True)
    configured: dict[str, str] = {}
    monkeypatch.setattr(resources.sys, "frozen", True, raising=False)
    monkeypatch.setattr(resources, "resource_root", lambda: tmp_path)
    monkeypatch.setattr(pyproj.datadir, "set_data_dir", lambda path: configured.__setitem__("pyproj", path))
    monkeypatch.setattr(
        rasterio.env,
        "set_proj_data_search_path",
        lambda path: configured.__setitem__("rasterio", path),
    )

    resources.configure_native_data_paths()

    assert configured == {"pyproj": str(pyproj_root), "rasterio": str(rasterio_root)}


def test_rasterio_environment_repairs_proj_path_after_env_enter(tmp_path: Path, monkeypatch) -> None:
    package_root = Path(rasterio.__file__).resolve().parent.parent
    invalid_proj_data = tmp_path / "invalid-proj-data"
    invalid_proj_data.mkdir()
    real_environment = rasterio.Env

    class ResettingEnvironment:
        def __init__(self, **options) -> None:
            self.environment = real_environment(**options)

        def __enter__(self):
            environment = self.environment.__enter__()
            rasterio.env.set_proj_data_search_path(str(invalid_proj_data))
            return environment

        def __exit__(self, *exc_info):
            return self.environment.__exit__(*exc_info)

    monkeypatch.setattr(resources, "resource_root", lambda: package_root)
    monkeypatch.setattr(rasterio, "Env", ResettingEnvironment)

    with resources.rasterio_environment():
        assert CRS.from_epsg(4547).to_epsg() == 4547
