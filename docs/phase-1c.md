# Phase 1C: GeoTIFF Foundations

Status: delivered with local development/distribution acceptance and Windows CI
passed. Application 0.4.0, public protocol 4, project schema 4, private worker
protocol 3. Acceptance uses synthetic data; see verification-phase-1c.md.

## Scope

- Inspect and import local self-contained GeoTIFF (.tif/.tiff, GDAL GTiff driver).
  Keep the original file unchanged; publish an immutable byte-for-byte managed
  snapshot at rasters/<dataset UUID>.tif, with SHA-256 identity and a bounded report.
- Preserve CRS, affine transform (including rotation), dimensions, resolution,
  bands, types, NoData, masks, color interpretation, descriptions, units,
  scale/offset, accessible tags and internal overviews. Metadata not exposed by
  the supported API is not claimed to have been interpreted; raw bytes survive.
- Unknown CRS may be imported as restricted data with metadata, but cannot be
  mapped or queried by geographic location. This phase does not assign a CRS.
  Reject GCP/RPC/geolocation-dependent sources and non-invertible affine grids.
- Reject detected external dependencies/known companion files (including .msk,
  .aux.xml, .ovr and worldfiles), complex-valued bands and unsupported containers.
  The single-file snapshot must not silently omit georeferencing or masks.
- Reject int64/uint64 NoData tags beyond JavaScript's safe integer range because
  the supported Rasterio API exposes those tags as floats. Ordinary 64-bit pixel
  values remain preserved and are returned as exact raw-value strings.
- Display a gray band or explicit RGB bands with per-channel raw-value ranges.
  Use nearest-neighbor display resampling. NoData, invalid/nonfinite pixels and
  masks control alpha; zero remains valid when the data says it is valid.
  Constant ranges are allowed and display valid pixels as middle gray.
- Show layers alongside vectors with shared order, visibility, opacity and fit.
  Persist raster style separately from vector color/category style. Tables have
  no map layer. A raster has no fabricated feature count, fields or vector IDs.
- Geographic click inspection returns the original grid cell (zero-based row
  and column), raw values as strings, validity/reason and scale/offset values.
  Unsafe integer conversion must not be presented as lossless numeric data.
- Export the complete original GeoTIFF snapshot to a new .tif/.tiff destination,
  preserve its hash and reopen it. Display PNGs are not analysis/export inputs.
  The existing export pipeline uses a project staging copy plus a destination
  pending copy; both volumes need free space for a full additional file.
- Retain cancellation, task history, publication/export recovery and project
  round trips. Migrate schema 1/2/3 with backups and preserve existing datasets,
  layers, metadata, tasks and journals.

## Resource Limits

- Source file: 512 MiB; each dimension: 100,000; bands: 16.
- Estimated complete decoded data: 2 GiB; each source block: 16 MiB.
- Serialized dataset metadata: 256 KiB; existing workspace/RPC bounds still apply.
- Inspection display statistics: at most 256 x 256 sampled cells per band.
  sampleMin/sampleMax are display suggestions, never full-data statistics.
- Render request: width/height 1-1024, finite increasing EPSG:3857 bbox, bounded
  global Mercator coordinates. GDAL cache and warp budgets: 64 MiB each.
- Read original pixels with OVERVIEW_LEVEL=NONE so an existing averaged overview
  cannot invent categories despite a nearest-neighbor request. Internal overview
  bytes/metadata are retained, but display does not use them in this milestone.
- Pin affine 2.4.0 for Rasterio 1.4.3 compatibility; no global warning suppression.
- Response envelope: at most 8 MiB. PNG RGBA generation uses Rasterio MemoryFile;
  no HTTP server, extra image library, arbitrary file URL or executable rendering payload.
- Import checks complete block readability within these limits, but does not
  claim positional accuracy, vertical-datum correctness or analysis suitability.

## Interfaces

The authoritative public shapes are in shared/contracts.ts. RasterDataset uses
common identity/source/CRS/bounds/report fields plus raster: RasterInfo.
MapLayer.rasterStyle is required for rasters and absent for existing vectors.

| RPC | Parameters | Result |
| --- | --- | --- |
| raster.inspect | sourcePath | RasterInspection |
| raster.import | path, sourcePath | Task |
| raster.export | path, datasetId, destination | Task |
| raster.render | path, datasetId, bbox, width, height, style | RasterRenderResult |
| raster.sample | path, datasetId, coordinate | RasterSampleResult |

Render bbox and returned image extent are EPSG:3857. Sample coordinate is
[longitude, latitude] in EPSG:4326. Style is {mode: gray/rgb, bands: number[],
ranges: [min,max][], resampling: nearest}; band indices are one-based. Output
is display-only and never overwrites or resamples the managed GeoTIFF.

Python rasters.py exposes inspect_raster(params), import_raster(payload,
work_dir, *, progress, cancelled), verify_snapshot(path, dataset, cancelled),
export_raster(payload, work_dir, *, progress, cancelled), default_style(dataset),
validate_style(style, dataset), render(dataset, managed_path, params), and
sample(dataset, managed_path, params). Import payload includes datasetId.
Export payload reuses dataset/managedPath; the destination pending path is supplied
separately as worker publishPath. Worker artifacts are
snapshot.tif/export.tif; existing vector/table artifacts remain GeoPackages.

## Acceptance and Boundaries

Check real Rasterio IO for projected/geographic/unknown CRS, affine rotation,
gray/RGB, masks/alpha/NoData/NaN, zero and negative values, scale/offset/units,
sampled statistics, outside-grid queries, invalid inputs, sidecars and budgets.
Decode rendered PNGs and verify pixels/alignment against independently known
synthetic cells. Verify immutable import/export, cancellation, recovery,
schema 1-3 migration, mixed-layer style persistence and native UI stale replies.

Run existing source and frozen vector/table workflows, new raster workflows,
frontend/engine/Rust tests, native screenshots and canvas-pixel checks, package
0.4.0 and test local installation/uninstallation. Record exact evidence and CI
for the functional commit before closeout. Real-data, ArcGIS, independent
Windows, offline/upgrade and large-scale performance verification remain open.

External-reference management, overview generation, raster calculations,
terrain analysis, remote sensing classification and publication/cartographic
exports remain later phases. Band/vertical metadata is retained for those phases.
