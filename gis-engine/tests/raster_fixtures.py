from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import ColorInterp
from rasterio.transform import from_origin


def create_raster_fixture_bundle(directory: Path) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    transform = from_origin(0, 4000, 1000, 1000)
    dem = directory / "elevation.tif"
    values = np.array([[0, 1, 2, 3, 4, 5], [6, -9999, 8, 9, 10, 11],
                       [12, 13, np.nan, np.inf, 16, 17], [18, 19, 20, 21, 22, 23]], dtype="float32")
    with rasterio.open(dem, "w", driver="GTiff", width=6, height=4, count=1, dtype="float32", crs="EPSG:3857", transform=transform, nodata=-9999) as source:
        source.write(values, 1)
        source.scales = (2.0,)
        source.offsets = (10.0,)
        source.set_band_description(1, "Recorded elevation")
        source.set_band_unit(1, "m")
        source.update_tags(AREA_OR_POINT="Area", source="synthetic known-value fixture")
        source.update_tags(1, quantity="elevation")
    rgb = directory / "imagery.tif"
    pixels = np.empty((4, 4, 6), dtype="uint8")
    pixels[0], pixels[1], pixels[2], pixels[3] = 200, 100, 50, 255
    pixels[3, 1, 1], pixels[3, 2, 2] = 0, 128
    with rasterio.open(rgb, "w", driver="GTiff", width=6, height=4, count=4, dtype="uint8", crs="EPSG:3857", transform=transform) as source:
        source.write(pixels)
        source.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha)
    masked = directory / "masked.tif"
    with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
        with rasterio.open(masked, "w", driver="GTiff", width=6, height=4, count=1, dtype="uint8", crs="EPSG:3857", transform=transform) as source:
            source.write(np.arange(24, dtype="uint8").reshape(4, 6), 1)
            mask = np.full((4, 6), 255, dtype="uint8")
            mask[1, 1] = 0
            source.write_mask(mask)
    return {"dem": str(dem), "rgb": str(rgb), "masked": str(masked)}
