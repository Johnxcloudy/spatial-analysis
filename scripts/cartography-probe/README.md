# CART-00 disposable rendering probes

These scripts compare two rendering candidates using synthetic GIS files. They
do not add a product Layout, export command, public protocol or project schema.
Install the pinned, hash-checked experiment dependencies in a separate environment:

```powershell
uv venv .artifacts/cartography-probe-env --python gis-engine/.venv/Scripts/python.exe
uv pip sync --python .artifacts/cartography-probe-env/Scripts/python.exe --require-hashes scripts/cartography-probe/requirements.txt
.artifacts/cartography-probe-env/Scripts/python.exe scripts/cartography-probe/probe.py --output .artifacts/cartography-probe-source --font C:/Windows/Fonts/simhei.ttf
node scripts/cartography-probe/verify-openlayers.mjs --fixture-dir .artifacts/cartography-probe-source --output .artifacts/cartography-probe-openlayers --font C:/Windows/Fonts/simhei.ttf
pwsh -File scripts/cartography-probe/freeze.ps1
.artifacts/cartography-probe-freeze/dist/cartography-probe/cartography-probe.exe --fixture-dir .artifacts/cartography-probe-source --output .artifacts/cartography-probe-frozen --font C:/Windows/Fonts/simhei.ttf
.artifacts/cartography-probe-env/Scripts/python.exe scripts/cartography-probe/compare.py --source .artifacts/cartography-probe-source --frozen .artifacts/cartography-probe-frozen --openlayers .artifacts/cartography-probe-openlayers --output .artifacts/cartography-probe-comparison.json
node scripts/cartography-probe/verify-identities.mjs --fixture-dir .artifacts/cartography-probe-source --output .artifacts/cartography-probe-identity-negative --font C:/Windows/Fonts/simhei.ttf --different-font .artifacts/cartography-probe-env/Lib/site-packages/matplotlib/mpl-data/fonts/ttf/DejaVuSans.ttf
```

Run from the repository root. The frontend dependencies and Microsoft Edge must
already be installed. Choose new output paths for repeated runs. `freeze.ps1`
accepts `-Destination` below `.artifacts`; it never overwrites an earlier bundle.
The server uses a loopback ephemeral port and closes after its Playwright checks.
The explicit font must be a local TrueType file containing the fixture glyphs.
The example Windows font is never copied into the bundle or repository; its
embedding flags do not establish permission to redistribute its original file.

The Python probe reads the full 2,103-feature GPKG, reprojects from EPSG:4326 to
EPSG:32650, renders a NoData-bearing GeoTIFF with polygons, and checks PNG pixels,
PDF paths/image/font/text and physical page size. It writes canonical projected
GeoJSON solely for the OpenLayers experiment. The browser route consumes that
complete file rather than the product's limited interactive viewport RPC.
Before launching its browser, the runner independently hashes the supplied
font, canonical scene, display raster and GPKG/GeoTIFF files against the Python
report. It serves the exact verified buffers and records their byte counts,
hashes and request counts. The comparison requires those actual served assets
to match both source and frozen Python output manifests. The negative runner
substitutes a font and modifies copied scene/raster/source assets; all four
cases must fail before browser startup without changing the original fixtures.

A4 landscape is 297 x 210 mm at a requested 300 DPI. The Python PNG backend
floors physical pixels to 3507 x 2480; the browser uses 3508 x 2480. The declared
tolerance is one pixel. The tested browser Canvas PNG has no embedded DPI
metadata, so its manifest records requested paper/DPI; formal export would need
metadata normalization. The PDF is a true A4 document with vector
paths and embedded text plus one raster image. It is not an all-vector map.

The isolated frozen bundle includes PDF inspectors and the existing GIS wheel
dependencies. Its total size is an experiment measurement, not the incremental
size of a future product renderer. A local frozen EXE using an external local
font is not independent-Windows installation acceptance.

The post-render 120-second assertion is an experiment deadline measurement, not
a task cancellation system. Feature/vertex/pixel budgets are checked before
rendering; the 36 MB bound covers the output RGBA buffer only, not total process
memory. Streaming, cancellation, failure publication, large canvases beyond A4,
other projections, and future Cartography Spec validation require later work.

Design and observed evidence: `docs/cartography-spec-draft.md` and
`docs/verification-cartography-probe.md`.
