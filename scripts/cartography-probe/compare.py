"""Cross-check probe reports and independently measure PDF scale control paths."""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
from pypdf import PdfReader
from pypdf.generic import ContentStream


def control_length(path):
    reader = PdfReader(path)
    operations = ContentStream(reader.pages[0].get_contents(), reader).operations
    lengths = []
    previous = None
    for values, operation in operations:
        if operation == b"l" and previous is not None:
            length = (float(values[0]) - previous[0]) / 72 * 25.4
            if abs(float(values[1]) - previous[1]) < 1e-6 and abs(length - 100) < 1e-5:
                lengths.append(length)
        previous = tuple(map(float, values)) if operation == b"m" else None
    assert len(lengths) == 1, f"Expected exactly one 100 mm grid control, found {lengths}"
    return lengths[0]


parser = argparse.ArgumentParser()
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--frozen", type=Path, required=True)
parser.add_argument("--openlayers", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
source = json.loads((args.source / "python-report.json").read_text(encoding="utf-8"))
frozen = json.loads((args.frozen / "python-report.json").read_text(encoding="utf-8"))
browser = json.loads((args.openlayers / "openlayers-report.json").read_text(encoding="utf-8"))
assert source["ok"] and frozen["ok"] and browser["ok"] and frozen["packaged"]
assert browser["identityVerified"], "Browser input bytes were not independently verified"
assert source["inputSha256"] == frozen["inputSha256"] == browser["inputSha256"]
assert source["font"]["sha256"] == frozen["font"]["sha256"] == browser["fontSha256"]
assert browser["servedAssets"]["/font.ttf"]["sha256"] == browser["fontSha256"]
for name, route in [("scene.json", "/fixture.json"), ("raster.png", "/raster.png")]:
    actual = browser["servedAssets"][route]
    assert actual["requests"] >= 1, f"The browser did not receive {name}"
    for report in [source, frozen]:
        expected = report["output"]["artifacts"][name]
        assert actual["sha256"] == expected["sha256"] and actual["bytes"] == expected["bytes"], f"Derived input differs: {name}"
assert browser["servedAssets"]["/font.ttf"]["requests"] >= 1
assert source["data"] == frozen["data"] and source["versions"] == frozen["versions"]
with Image.open(args.source / "python.png") as first, Image.open(args.frozen / "python.png") as second:
    exact = bool(np.array_equal(np.asarray(first), np.asarray(second)))
assert exact, "Source and frozen PNG pixels differ"
colors = []
for check in source["output"]["png"]["checks"]:
    actual = next(item for item in browser["checks"] if item["name"] == check["name"])
    maximum = int(np.max(np.abs(np.array(check["actual"]) - np.array(actual["actual"][:3]))))
    assert maximum <= 2, f"Renderer control differs: {check['name']}"
    colors.append({"name": check["name"], "maxRendererChannelDifference": maximum})
result = {"ok": True, "sameInputsFontAndVersions": True, "actualServedAssetsVerified": True,
          "sourceFrozenPngPixelsIdentical": exact,
          "pdfControlLengthMm": {"source": control_length(args.source / "python.pdf"),
                                 "frozen": control_length(args.frozen / "python.pdf")},
          "openlayersControlLengthMm": browser["physicalControlPixels"] / 300 * 25.4,
          "colorComparison": colors, "sources": [str(args.source), str(args.frozen), str(args.openlayers)]}
with Image.open(args.openlayers / "openlayers.png") as image:
    assert image.size == (3508, 2480)
    result["openlayersPng"] = {"pixels": image.size, "embeddedDpi": image.info.get("dpi")}
args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result))
