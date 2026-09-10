# CART-00 rendering probe evidence

Date: 2026-09-10. Status: the Phase 1D disposable experiment passed locally.
This is not delivery of a product Layout or formal cartographic export feature.
Execution commands are in [the probe README](../scripts/cartography-probe/README.md).
Design is in [the draft spec](cartography-spec-draft.md).

## Fixture and acceptance

Both routes consumed the same full synthetic dataset: a real GPKG storing 2,103
features/10,525 vertices in EPSG:4326, transformed with PyProj to EPSG:32650, and
a 100 x 68 RGB GeoTIFF with NoData. The last three features are a polygon with a
hole, a multipart polygon and a final sentinel. All original file SHA-256 values
remained unchanged. The projection round-trip control error was
`4.656612873077393e-10` m.

The requested page was A4 landscape, 297 x 210 mm, 300 DPI. Before execution,
the scripts fixed seven color controls and a maximum 3-channel color error of
3, one-pixel physical-dimension rounding tolerance, 3,000 features, 20,000
vertices, 9,000,000 pixels, and 36,000,000 bytes for a single output RGBA buffer.
The 120-second post-render check measures this experiment; it does not implement
hard cancellation or bound total process memory.

| Check | Actual observed result |
| --- | --- |
| Full input | 2,103 unique feature IDs supplied to OpenLayers; 2,103 polygons read/rendered by Python; no interactive viewport RPC used |
| Holes, multipart, transparency, last feature, NoData | All seven color controls passed in Python PNG, PDF rasterized with PDFium and OpenLayers PNG |
| Python PNG | 3507 x 2480 pixels; embedded DPI 299.9994 x 299.9994; backend floors the exact physical width by one pixel |
| OpenLayers PNG | 3508 x 2480 pixels; independently inspected with Pillow: no embedded DPI metadata |
| PDF paper | 296.9999999999903 x 210.00000000000665 mm |
| PDF objects | 2,104 filled paths including page background; one 2953 x 2008 raster Image XObject; vector text and polygon paths retained |
| PDF font | `/DABZEG+SimHei`, embedded TrueType subset and ToUnicode mapping; title and all three Chinese labels extracted successfully |
| Independent PDF control length | 100.00000002500002 mm for the projected 1,000 m line, both source and frozen outputs |
| OpenLayers control length | 100.00826666667258 mm from actual map coordinate/pixel transform, within the declared one-pixel tolerance |
| Renderer agreement | Seven controls differ by at most 1 channel value between Python and OpenLayers |
| Frozen equivalence | Source and frozen PNG pixels are exactly equal; matching inputs, font hash, data metadata and library versions |
| Missing font glyphs | DejaVu Sans was deliberately supplied; probe failed before fixture generation/rendering, reported 20 missing Chinese glyphs and `ok=false` |
| Actual browser asset identity | Final2 independently hashes the supplied font, scene, raster PNG and source GPKG/GeoTIFF; scene/raster hashes and lengths match both source and frozen Python manifests |
| Browser identity rejection | Changed font, scene title, raster PNG and source GPKG each fail with exit 1, `ok=false`, `identityVerified=false`, `browserStarted=false`; originals unchanged |

The grid scale is explicitly 1:10,000 in projected coordinate metres. The probe
does not validate a true-north arrow, arbitrary map rotation, ground-distance
scale, or use of EPSG:3857 coordinate metres as ground distance.

## Browser and visual evidence

OpenLayers 10.10.0, Playwright 1.63.0 and Microsoft Edge 152.0.4191.66 ran a
separate renderer through a loopback ephemeral server. Font loading, image
decoding and OpenLayers `rendercomplete` were awaited. Playwright inspected the
actual output pixels and preview layout at 1400 x 1000 and 390 x 844. Both
previews were nonblank, within the viewport, and had no horizontal overflow;
there were no page errors. The server and browser were closed after testing.

Visually inspected images include the Python PNG preview, PDFium PDF preview,
and desktop/mobile OpenLayers screenshots. Chinese titles/labels were legible
at desktop output size, with the expected hole, both multipart components,
NoData gap and complete grid. The mobile artifact is an entire A4 sheet reduced
to fit; it is a framing check, not a claim that print text is readable without
zooming on a small screen.

Final browser evidence:

- `.artifacts/cartography-probe-openlayers-final2/openlayers-report.json`
- `.artifacts/cartography-probe-openlayers-final2/openlayers.png`
- `.artifacts/cartography-probe-openlayers-final2/desktop.png`
- `.artifacts/cartography-probe-openlayers-final2/mobile.png`

### Identity verification correction

Review found that the earlier browser runner copied `fontSha256` and
`inputSha256` from the Python report. A reviewer reproduced a different supplied
font passing the old browser and comparison checks, so those earlier records
do not establish independently verified browser input identity. The original
artifacts, including the negative reproduction, remain available as history.

The corrected runner hashes the actual supplied files before browser startup.
It reads scene/raster/font bytes once, validates them against the Python output
manifest and serves those exact buffers, recording their hashes, lengths and
request counts. It independently hashes the GPKG and GeoTIFF in the supplied
fixture directory. The comparison requires the actual served scene/raster to
match both source and frozen Python manifests. Browser font fallback can no
longer turn a substituted font into a matching identity claim.

Final2 served each of the three resources once: scene 1,701,138 bytes, raster
PNG 278 bytes and local SimHei 9,745,792 bytes. The final comparison reports
`actualServedAssetsVerified=true`. Four executable negative cases replace the
font, edit the copied scene title, replace the copied raster with another valid
PNG and append bytes to the copied GPKG. All four are rejected before server
or browser startup; the original fixture hashes remain unchanged. Evidence:
`.artifacts/cartography-probe-identity-negative-final2/negative-report.json`.
Final2 screenshots match the previously visually inspected screenshots byte for
byte. No Python rendering code or frozen binary changed in this correction, so
their original execution evidence remains applicable rather than being claimed
as newly executed.

## Isolated Python and freezing

The production engine environment, `pyproject.toml`, `uv.lock`, public RPC and
project schema were not changed by this experiment. A separate 30-package
environment was installed from `scripts/cartography-probe/requirements.txt`,
which pins all resolved versions and distribution hashes. Direct selections:
Matplotlib 3.10.6, Pillow 11.3.0, fontTools 4.60.0, pypdf 6.0.0, PDFium wrapper
4.30.0, PyInstaller 6.16.0, and the existing exact GIS/NumPy/Arrow versions.

A standalone onedir probe was built with the existing GIS wheel hooks and an
explicit Matplotlib PDF backend. It ran with PATH reduced to Windows system
directories and deliberately invalid PYTHONHOME, PYTHONPATH, PROJ_DATA,
PROJ_LIB and GDAL_DATA. The EXE selected bundled GIS resources and returned
`ok=true`, `packaged=true`. Measured in-process execution was 9.703 seconds for
the source run including fixture creation and 4.594 seconds for the frozen run
using existing fixtures; these are different workloads, not a speed comparison.

The standalone directory contains 1,616 files, 320,081,478 bytes. It includes
GIS libraries and PDF verification tools, so this total is not the incremental
size of a production renderer. No Chinese font was copied into it. The build
log contains a warning about an absent conda-style PyProj data directory; the
wheel's actual bundled PROJ resources were found and passed the isolated run.

EXE:
`.artifacts/cartography-probe-freeze/dist/cartography-probe/cartography-probe.exe`

SHA-256:
`5C6EBEAF79C5BD9D3A62A19BB0F0FAD4971282BF301C8FCB284AE7076D85EEAE`

Source and freeze evidence:

- `.artifacts/cartography-probe-source/python-report.json`
- `.artifacts/cartography-probe-source/python-preview.png`
- `.artifacts/cartography-probe-source/pdf-preview.png`
- `.artifacts/cartography-probe-freeze/build.log`
- `.artifacts/cartography-probe-freeze/freeze-report.json`
- `.artifacts/cartography-probe-frozen/python-report.json`
- `.artifacts/cartography-probe-comparison-final2.json`
- `.artifacts/cartography-probe-rejected-font/python-report.json` (expected rejection)

The explicit local font was `C:\Windows\Fonts\simhei.ttf`, SHA-256
`9b1959db3b3abeb7efdaec26edf7dfe871a6039de8d614af7248575207be629e`.
Its OS/2 fsType was 8 and all requested glyphs were present. Embedding this
tested subset does not establish permission to redistribute the original font.
An approved redistributable font and fallback policy remain product work.

## Decision and limits

The evidence supports evaluating OpenLayers for a separate complete-data raster
preview/export path and Python for mixed vector/raster documents. This is a
provisional bounded combination, not a dependency lock-in for the application.
The Python PDF retains vector geometry and searchable text, while its background
remains raster. That image is resampled to output pixels; a 100 x 68 input does
not gain spatial detail by being embedded as 2953 x 2008 pixels. Browser PNG
needs explicit physical-resolution metadata before formal export.

Outstanding work includes executable Cartography Spec validation/versioning,
managed-resource resolution, classification and legends, scale/direction rules,
preview/export style parity beyond these controls, label collisions, approved
fonts, tasks/cancellation/publication recovery, A3 and larger canvas/memory
budgets, real data, and independent Windows installation without development
tools. No production installer was changed to carry this probe. No SVG,
all-vector PDF, Nature compliance, offline installation, or Agent feature was
accepted here. Normal-map overlays and source relocation belong to other
Phase 1D work and are not covered by this experiment.
