"""Disposable CART-00 renderer and inspection; not a product export API."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from importlib.metadata import version

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.colors import to_rgb
import numpy as np
import pyarrow as pa
import pyogrio
import pyproj
import rasterio
from rasterio.transform import from_bounds
import shapely
from shapely.geometry import Polygon, MultiPolygon, box, mapping
from shapely.plotting import patch_from_polygon
from fontTools.ttLib import TTFont
from PIL import Image
from pypdf import PdfReader
from pypdf.generic import ContentStream
import pypdfium2


CRS = "EPSG:32650"
EXTENT = [500000, 3000000, 502500, 3001700]
PAPER_MM = [297, 210]
FRAME_MM = [23.5, 25, 250, 170]  # left, bottom, width, height
DPI = 300
PIXELS = [round(mm / 25.4 * DPI) for mm in PAPER_MM]
TITLE = "\u571f\u5730\u5229\u7528\u5408\u6210\u6837\u672c"
LABELS = ["\u5e26\u6d1e\u5730\u5757", "\u591a\u90e8\u4ef6\u5730\u5757", "\u5b8c\u6574\u6570\u636e\u672b\u9879"]
COLORS = {"grid": "#429b7a", "hole": "#4477aa", "multipart": "#aa4499", "sentinel": "#cc6677"}
ALPHA = 0.65
FOOTER = "Synthetic data | EPSG:32650 | grid scale 1:10,000 | 2,103 features"
BUDGET = {"maxFeatures": 3000, "maxVertices": 20000, "maxOutputPixels": 9_000_000,
          "maxRgbaBytes": 36_000_000, "deadlineSeconds": 120}


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def check(condition: bool, detail: str) -> None:
    if not condition:
        raise AssertionError(detail)


def configure_frozen() -> None:
    if not getattr(sys, "frozen", False):
        return
    root = Path(sys._MEIPASS)
    pyproj.datadir.set_data_dir(str(root / "pyproj" / "proj_dir" / "share" / "proj"))
    # These wheels carry separate GDAL/PROJ builds. Give Rasterio its own resources.
    os.environ["PROJ_DATA"] = str(root / "rasterio" / "proj_data")
    os.environ["GDAL_DATA"] = str(root / "rasterio" / "gdal_data")
    rasterio.env.set_proj_data_search_path(os.environ["PROJ_DATA"])


def create_fixture(directory: Path) -> None:
    projected = [box(500050 + col * 33, 3000050 + row * 25,
                     500076 + col * 33, 3000068 + row * 25)
                 for row in range(30) for col in range(70)]
    projected += [
        Polygon(box(500150, 3000950, 500750, 3001500).exterior.coords,
                [box(500300, 3001100, 500600, 3001350).exterior.coords]),
        MultiPolygon([box(500950, 3001000, 501250, 3001400),
                      box(501400, 3001150, 501650, 3001500)]),
        box(501850, 3001150, 502300, 3001450),
    ]
    categories = ["grid"] * 2100 + ["hole", "multipart", "sentinel"]
    inverse = pyproj.Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    geographic = shapely.transform(np.array(projected, dtype=object), inverse.transform, interleaved=False)
    table = pa.table({"id": list(range(1, 2104)), "category": categories,
                      "label": [""] * 2100 + LABELS,
                      "geometry": pa.array(shapely.to_wkb(geographic).tolist(), type=pa.binary())})
    pyogrio.write_arrow(table, directory / "land.gpkg", layer="land", driver="GPKG",
                        geometry_name="geometry", geometry_type="Unknown", crs="EPSG:4326")
    values = np.empty((3, 68, 100), dtype=np.uint8)
    values[:] = np.array([220, 230, 210], dtype=np.uint8)[:, None, None]
    values[:, :, 50:] = np.array([180, 210, 230], dtype=np.uint8)[:, None, None]
    values[:, :6, 32:40] = 0
    with rasterio.open(directory / "background.tif", "w", driver="GTiff", width=100,
                       height=68, count=3, dtype="uint8", crs=CRS,
                       transform=from_bounds(*EXTENT, 100, 68), nodata=0) as raster:
        raster.write(values)
    save_json(directory / "fixture.json", {"count": 2103, "projectedWkbSha256":
        hashlib.sha256(b"".join(shapely.to_wkb(projected))).hexdigest(), "synthetic": True})


def load_fixture(directory: Path) -> tuple[list, list, list, np.ndarray, dict]:
    meta, table = pyogrio.read_arrow(directory / "land.gpkg", layer="land")
    geometries = shapely.from_wkb(table[meta["geometry_name"]].to_pylist())
    transformer = pyproj.Transformer.from_crs(meta["crs"], CRS, always_xy=True)
    projected = shapely.transform(geometries, transformer.transform, interleaved=False)
    categories = table["category"].to_pylist()
    labels = table["label"].to_pylist()
    check(len(projected) == 2103, "full GPKG read did not contain 2,103 features")
    check(bool(np.all(shapely.is_valid(projected))), "invalid fixture geometry")
    vertices = int(shapely.get_num_coordinates(projected).sum())
    check(len(projected) <= BUDGET["maxFeatures"] and vertices <= BUDGET["maxVertices"], "data budget exceeded")
    control = transformer.transform(*pyproj.Transformer.from_crs(CRS, meta["crs"], always_xy=True).transform(500000, 3000000))
    error = math.dist(control, [500000, 3000000])
    check(error < 1e-6, "projection round-trip control exceeded one micrometre")
    with rasterio.open(directory / "background.tif") as raster:
        check(raster.crs == rasterio.crs.CRS.from_string(CRS), "raster CRS differs")
        rgba = np.dstack([raster.read().transpose(1, 2, 0), raster.dataset_mask()])
    return list(projected), categories, labels, rgba, {"featureCount": len(projected),
        "vertexCount": vertices, "projectionRoundTripErrorM": error,
        "sourceCrs": meta["crs"], "mapCrs": CRS, "fullData": True}


def font_report(path: Path) -> dict:
    with TTFont(path) as font:
        cmap = font.getBestCmap()
        text = TITLE + "".join(LABELS) + FOOTER
        missing = sorted({char for char in text if not char.isspace() and ord(char) not in cmap})
        check(not missing, f"font has missing glyphs: {missing}")
        embedding = int(font["OS/2"].fsType)
        check(not embedding & 0x0002, "font embedding is restricted by OS/2 fsType")
    return {"path": str(path), "sha256": sha(path), "missingGlyphs": missing,
            "fsType": embedding, "redistribution": "Not authorized; external local font, never bundled."}


def render(directory: Path, geometries: list, categories: list, labels: list,
           rgba: np.ndarray, font: Path) -> dict:
    check(PIXELS[0] * PIXELS[1] <= BUDGET["maxOutputPixels"], "output budget exceeded")
    check(PIXELS[0] * PIXELS[1] * 4 <= BUDGET["maxRgbaBytes"], "RGBA budget exceeded")
    matplotlib.rcParams.update({"pdf.fonttype": 42, "pdf.compression": 6, "svg.fonttype": "path"})
    prop = FontProperties(fname=str(font))
    fig = plt.figure(figsize=(PAPER_MM[0] / 25.4, PAPER_MM[1] / 25.4), dpi=DPI, facecolor="white")
    x, y, width, height = FRAME_MM
    ax = fig.add_axes([x / PAPER_MM[0], y / PAPER_MM[1], width / PAPER_MM[0], height / PAPER_MM[1]])
    ax.set_xlim(EXTENT[0], EXTENT[2])
    ax.set_ylim(EXTENT[1], EXTENT[3])
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.imshow(rgba, extent=[EXTENT[0], EXTENT[2], EXTENT[1], EXTENT[3]],
              origin="upper", interpolation="nearest", zorder=0)
    for geometry, category in zip(geometries, categories):
        ax.add_patch(patch_from_polygon(geometry, facecolor=COLORS[category], edgecolor=COLORS[category],
                                       linewidth=0.3, alpha=ALPHA, zorder=1))
    for xy, label in zip([(500450, 3001535), (501300, 3001535), (502075, 3001535)], LABELS):
        ax.text(*xy, label, fontproperties=prop, fontsize=9, ha="center", va="bottom", zorder=2)
    fig.text(0.5, 0.955, TITLE, fontproperties=prop, fontsize=16, ha="center", va="center")
    fig.text(x / PAPER_MM[0], 10 / PAPER_MM[1], FOOTER, fontproperties=prop, fontsize=8)
    # Declare this as a projected grid distance. It is not an everywhere-true ground scale.
    ax.plot([500100, 501100], [3000870, 3000870], color="#202020", linewidth=1, zorder=3)
    ax.text(500600, 3000900, "1,000 m (grid)", fontproperties=prop, fontsize=8, ha="center", zorder=3)
    fig.savefig(directory / "python.png", dpi=DPI)
    fig.savefig(directory / "python.pdf", dpi=DPI, metadata={"Title": TITLE, "Creator": "CART-00 probe"})
    plt.close(fig)
    Image.fromarray(rgba).save(directory / "raster.png")
    save_json(directory / "scene.json", {"draftVersion": "cart-00-probe/1", "crs": CRS,
        "extent": EXTENT, "paperMm": PAPER_MM, "frameMm": FRAME_MM, "dpi": DPI, "pixels": PIXELS,
        "title": TITLE, "footer": FOOTER, "labels": [{"coordinate": xy, "text": text} for xy, text in
            zip([(500450, 3001535), (501300, 3001535), (502075, 3001535)], LABELS)],
        "opacity": ALPHA, "colors": COLORS, "budget": BUDGET,
        "collection": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "id": index + 1, "properties": {"category": category, "label": label},
             "geometry": mapping(geometry)} for index, (geometry, category, label) in
            enumerate(zip(geometries, categories, labels))]}})
    return inspect_outputs(directory)


def projected_pixel(point: tuple[float, float], size: tuple[int, int]) -> tuple[int, int]:
    x, y, width, height = FRAME_MM
    px = (x + (point[0] - EXTENT[0]) / (EXTENT[2] - EXTENT[0]) * width) / PAPER_MM[0] * size[0]
    py = (PAPER_MM[1] - y - (point[1] - EXTENT[1]) / (EXTENT[3] - EXTENT[1]) * height) / PAPER_MM[1] * size[1]
    return round(px), round(py)


def pixel_checks(image: Image.Image) -> list:
    image = image.convert("RGB")
    base = np.array([220, 230, 210])
    other = np.array([180, 210, 230])
    blend = lambda category, background: np.array(to_rgb(COLORS[category])) * 255 * ALPHA + background * (1 - ALPHA)
    probes = [("hole", (500450, 3001200), base), ("hole-shell", (500200, 3001200), blend("hole", base)),
              ("multipart-first", (501100, 3001200), blend("multipart", base)),
              ("multipart-second", (501500, 3001300), blend("multipart", other)),
              ("feature-2103", (502075, 3001300), blend("sentinel", other)),
              ("NoData", (500900, 3001600), np.array([255, 255, 255])),
              ("grid", (500063, 3000059), blend("grid", base))]
    results = []
    for name, coordinate, expected in probes:
        actual = np.array(image.getpixel(projected_pixel(coordinate, image.size)))
        error = float(np.abs(actual - expected).max())
        check(error <= 3, f"{name} color failed: {actual} vs {expected}")
        results.append({"name": name, "actual": actual.tolist(), "maxChannelError": error})
    return results


def inspect_outputs(directory: Path) -> dict:
    with Image.open(directory / "python.png") as image:
        dimensions = image.size
        # Matplotlib floors the exact physical canvas; allow the specified nearest-pixel rounding.
        check(all(abs(actual - target) <= 1 for actual, target in zip(dimensions, PIXELS)), "PNG dimensions differ")
        dpi = image.info.get("dpi")
        check(dpi is not None and max(abs(item - DPI) for item in dpi) < 0.1, "PNG DPI metadata differs")
        png_checks = pixel_checks(image)
        image.thumbnail((1200, 1200))
        image.save(directory / "python-preview.png")
    reader = PdfReader(directory / "python.pdf")
    check(len(reader.pages) == 1, "PDF page count differs")
    page = reader.pages[0]
    paper = [float(page.mediabox.width) / 72 * 25.4, float(page.mediabox.height) / 72 * 25.4]
    check(max(abs(actual - target) for actual, target in zip(paper, PAPER_MM)) < 1e-6, "PDF paper size differs")
    operations = ContentStream(page.get_contents(), reader).operations
    fill_paths = sum(op in [b"f", b"f*", b"B", b"B*"] for _, op in operations)
    check(fill_paths >= 2103, "PDF did not retain the full vector path population")
    xobjects = page["/Resources"].get("/XObject", {}).get_object()
    images = [obj.get_object() for obj in xobjects.values() if obj.get_object().get("/Subtype") == "/Image"]
    check(len(images) == 1, "PDF must contain the raster background as one image")
    fonts = []
    for obj in page["/Resources"]["/Font"].get_object().values():
        font = obj.get_object()
        descendant = font["/DescendantFonts"][0].get_object()
        embedded = "/FontFile2" in descendant["/FontDescriptor"].get_object()
        unicode_map = "/ToUnicode" in font
        check(embedded and unicode_map, "PDF requires embedded TrueType and ToUnicode")
        fonts.append({"baseFont": str(font["/BaseFont"]), "embeddedTrueType": embedded, "toUnicode": unicode_map})
    extracted = page.extract_text()
    check(all(text in extracted for text in [TITLE, *LABELS]), "Chinese PDF text cannot be extracted")
    pdf = pypdfium2.PdfDocument(directory / "python.pdf")
    try:
        bitmap = pdf[0].render(scale=DPI / 72)
        try:
            rendered = bitmap.to_pil()
            pdf_checks = pixel_checks(rendered)
            rendered.thumbnail((1200, 1200))
            rendered.save(directory / "pdf-preview.png")
        finally:
            bitmap.close()
    finally:
        pdf.close()
    return {"png": {"pixels": dimensions, "dpi": dpi, "checks": png_checks},
            "pdf": {"paperMm": paper, "filledPaths": fill_paths, "images": len(images),
                    "imagePixels": [[int(item["/Width"]), int(item["/Height"])] for item in images],
                    "fonts": fonts, "chineseTextExtracted": True, "checks": pdf_checks,
                    "kind": "vector paths and text with raster background"},
            "artifacts": {name: {"bytes": (directory / name).stat().st_size, "sha256": sha(directory / name)}
                          for name in ["python.png", "python.pdf", "scene.json", "raster.png"]}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--fixture-dir", type=Path)
    parser.add_argument("--font", required=True, type=Path)
    args = parser.parse_args()
    started = time.monotonic()
    configure_frozen()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {"ok": False, "packaged": bool(getattr(sys, "frozen", False)), "budget": BUDGET}
    try:
        font = font_report(args.font.resolve())
        fixture = args.fixture_dir.resolve() if args.fixture_dir else output
        if not args.fixture_dir:
            create_fixture(fixture)
        original = {name: sha(fixture / name) for name in ["land.gpkg", "background.tif"]}
        geometries, categories, labels, rgba, data = load_fixture(fixture)
        rendered = render(output, geometries, categories, labels, rgba, args.font.resolve())
        check(original == {name: sha(fixture / name) for name in original}, "render mutated source bytes")
        elapsed = time.monotonic() - started
        check(elapsed < BUDGET["deadlineSeconds"], "probe exceeded declared deadline")
        report.update({"ok": True, "fixtureDirectory": str(fixture), "inputSha256": original,
                       "font": font, "data": data, "output": rendered, "elapsedSeconds": elapsed,
                       "gridControl": {"coordinateDistanceM": 1000, "paperDistanceMm": 100,
                                       "scaleDenominator": 10000, "meaning": "projected grid distance only"},
                       "versions": {name: version(name) for name in ["matplotlib", "pyogrio", "pyproj",
                            "rasterio", "shapely", "numpy", "Pillow", "pypdf", "pypdfium2", "fonttools"]}})
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        save_json(output / "python-report.json", report)
    print(json.dumps({"ok": report["ok"], "packaged": report["packaged"], "report": str(output / "python-report.json")}))


if __name__ == "__main__":
    main()
