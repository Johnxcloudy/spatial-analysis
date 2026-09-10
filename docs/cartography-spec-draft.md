# Cartography Spec: CART-00 draft

Status: experimental contract design for Phase 1D, 2026-09-10. This document is
not a public RPC contract, project schema, implemented validator or saved Layout.
`shared/contracts.ts` remains the owner of current public application contracts.
See [the roadmap](cartography-roadmap.md) for future implementation stages and
[probe evidence](verification-cartography-probe.md) for observed capabilities.

## Proposed document boundary

One versioned document describes a reproducible picture of immutable datasets.
Manual controls, templates and optional Agent proposals would edit this same
document. A validated export task would resolve registered data/resources,
freeze the document and inputs, then generate and verify output before publishing.
Styles never edit input geometry, values, analysis CRS, units or statistics.

| Group | Minimum fields and invariants |
| --- | --- |
| Identity | `specVersion`, `id`, `revision`, `name`; reject newer unsupported versions, migrate older versions explicitly |
| Inputs | Dataset/Result ID, immutable version/SHA-256, kind, provenance reference; stable field references; reject missing or changed inputs |
| Page | Explicit `widthMm`, `heightMm`, background, sRGB; finite positive physical values |
| Map frame | ID, rectangle in mm, complete CRS WKT and optional authority, projected extent, rotation, distance convention; fit rectangle within page |
| Layers | Ordered list with unique IDs, input/version reference, visibility, opacity 0-1, `normal` blend; a tagged vector/raster symbol definition |
| Text/resources | Literal bounded text or validated field reference, font resource ID/hash, font size in pt, position/anchor; image resource IDs/hashes; no executable code or arbitrary path/URL |
| Decoration | Title/source/date, explicit legend entries, scale/direction conventions; output includes only supported validated types |
| Theme | Preset ID/version, overridable rules, provenance/source and review date; preset is not a compliance certificate |
| Output | PNG/PDF, DPI, explicit rounding rule, color space, feature/vertex/pixel/decoded-byte/time budgets and renderer capability version |

Vector styles initially need fill/stroke color, opacity, line width in pt and
optional explicit categories. A category stores its original code, label, color
and field reference. NULL, unmatched, excluded and valid zero are distinct
states. Future graduated styles must also store the method/version, complete
classification population, actual bounds/endpoint inclusion, labels and units.
Display filters, if added, must be stored as display filters with a count and
must not alter analysis populations or inherited statistical denominators.

Raster styles need selected bands, raw-value display bounds, explicit resampling,
mask/NoData policy and band scale/offset semantics. The first probe uses an RGB
GeoTIFF and nearest sampling. Terrain parameters, advanced blend modes and
online resources are reserved design topics; they are not silently accepted by
the first implementation.

Resources would be resolved through a controlled registry. A document references
font/image IDs and hashes, while Tauri grants access and Python validates actual
files. Font glyph coverage, embedding policy and missing-font behavior must be
checked before rendering. OS/2 embedding flags alone do not establish permission
to redistribute a font file. Agent proposals cannot supply rendering code,
arbitrary file paths, network requests or changes to authoritative analysis.

## Minimal proposed example

The following is design notation, not accepted application JSON. Symbol names
illustrate future fields; the probe's `scene.json` is an independent fixture
interchange file (`cart-00-probe/1`) and must not be migrated as a project spec.

```json
{
  "specVersion": "draft/0.1",
  "id": "example-layout",
  "revision": 1,
  "name": "Synthetic land-use sheet",
  "inputs": [
    { "id": "registered-dataset-id", "version": "registered-sha256", "kind": "vector" }
  ],
  "page": { "widthMm": 297, "heightMm": 210, "colorSpace": "sRGB", "background": "#ffffff" },
  "mapFrame": {
    "id": "main",
    "rectangleMm": [23.5, 25, 250, 170],
    "crs": { "authority": "EPSG:32650", "wkt": "registered complete CRS WKT" },
    "extent": [500000, 3000000, 502500, 3001700],
    "rotationDegrees": 0,
    "distanceConvention": "projected-grid"
  },
  "layers": [
    {
      "id": "land", "inputId": "registered-dataset-id", "visible": true,
      "opacity": 0.65, "blend": "normal",
      "symbol": { "kind": "polygon", "fill": "#429b7a", "stroke": "#429b7a", "strokePt": 0.3 }
    }
  ],
  "text": [
    { "text": "Synthetic land use", "fontId": "registered-font-resource", "sizePt": 16, "positionMm": [148.5, 200], "anchor": "center" }
  ],
  "output": { "format": "pdf", "dpi": 300, "pixelRounding": "nearest", "rendererCapability": "candidate-pdf-v1" }
}
```

The future validator must reject unknown fields, ambiguous symbol variants,
nonfinite values, out-of-page rectangles, missing fields/resources, unsupported
formats/effects and budgets beyond the renderer's supported limits. Never infer
a CRS, analytical unit, legal land classification standard or precision from a
filename. This validation remains future implementation, not a CART-00 claim.

## CRS, scale and full-data export

The probe uses a 2,500 x 1,700 m UTM map extent in a 250 x 170 mm map frame.
A 1,000 m projected grid control is therefore 100 mm on paper (1:10,000 grid
scale). This does not claim equal ground distances throughout the projection,
nor validate a true-north arrow, rotation, arbitrary projection or map scale
on EPSG:3857. Production scale/direction need their own explicit conventions and
independent control tests before being offered.

The formal export input must be a complete managed snapshot or a documented
explicit selection, read in bounded batches by a cancellable export task.
Neither the 2,000-feature/100,000-vertex interactive viewport nor its EPSG:3857
PNG is an authoritative export source. Exceeding an export budget must fail
explicitly rather than truncate. Previews may reduce resolution but retain
the exact same document and input identities.

The probe checks 3,000 features, 20,000 vertices, 9,000,000 output pixels and
36,000,000 bytes for one RGBA buffer. Its measured 120-second deadline is not a
production cancellation guarantee, and the buffer limit is not total process
memory. These small experimental limits do not become product export limits.

## Output manifest and provisional renderer decision

Each future export record needs: final spec hash/revision; input versions and
selection counts; actual physical/pixel dimensions; DPI and rounding; source/map
CRS and distance convention; renderer/library versions; font/resource hashes;
warnings; rasterized components/effective pixel resolution; completion and
publication identity. A PNG must carry appropriate resolution metadata. A PDF
extension alone says nothing about vector content or font portability.

Asset fingerprints must be measured from the bytes actually resolved and
rendered. Copying a fingerprint from a prior report cannot establish identity
for a different supplied font or a changed scene/image. CART-00 now verifies
the actual buffers before serving them and rejects mismatches before browser
startup; formal export needs the same check at its controlled resource boundary.

The observed candidate is a bounded combination: use an independent OpenLayers
renderer for style reuse and raster preview; evaluate Python document rendering
for formal mixed vector/raster PDF. Two renderers require explicit style and
geometry equivalence tests. The probe establishes basic feasibility only.
Dependencies remain isolated until Phase 4 acceptance includes labels, legends,
scale/direction, approved redistributable fonts, task recovery, large outputs and
an actual installation without development tools. No PyQGIS runtime is proposed
by this experiment, and no full-vector PDF/SVG promise is made.
