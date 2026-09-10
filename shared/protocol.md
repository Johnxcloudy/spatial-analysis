# Desktop engine protocol v6

Transport: persistent console sidecar, UTF-8 newline-delimited JSON-RPC 2.0. The host assigns request IDs and serializes calls. One response line per request. No protocol chatter or logs on stdout. A 90-second host deadline terminates an unresponsive process tree. Import/export/point/copy/relocation jobs execute in a child worker, while short task polling remains responsive. Methods not listed here are rejected. Responses use camelCase matching shared/contracts.ts. Protocol version is 6. Requests are limited to 1 MiB; responses to 8 MiB including the newline. Oversize responses return a domain error without losing the active project.

## Methods

- runtime.info, params {} -> RuntimeInfo. Load actual GIS libraries and report actual GDAL driver capabilities, not assumed availability.
- project.create, params {directory: string, name: string} -> Project. directory is the intended NEW project directory (may exist if project.spa does not). name is a nonempty display name. Create project.spa and owned subdirectories; never overwrite a project. Default description empty, analysisCrs null, displayCrs EPSG:3857, viewState {center:[114,27.1],zoom:5}. Engine holds a lock for the active project. Creation/open closes the previously active project only after the new operation succeeds.
- project.open, params {path: string} -> Project. path is an existing project.spa. Validate identity and schema before any writes; acquire an exclusive OS-backed lock, fail clearly if held by another engine. Back up schema v1/v2/v3/v4/v5 via SQLite backup, validate that backup, then migrate transactionally to v6. Reject future schema versions, corrupt files and incomplete project copies.
- project.save, params {path:string, name:string, description:string, analysisCrs:string|null, displayCrs:string, viewState:{center:[number,number],zoom:number}} -> Project. Must refer to the active project. Validate finite coordinates, zoom and CRS. Use an atomic SQLite transaction; preserve id/createdAt and update updatedAt. Invalid inputs must not change the project.
- project.close, params {} -> {closed:true}. Release active project lock.
- diagnostics.run, params {directory:string} -> ProbeReport. directory is for generated diagnostic artifacts (a cache folder, not original inputs). Create a unique subdirectory per run. Test a 100m x 100m rectangle at x=500000, y=3000000 in EPSG:4547; overlay with a 50m-shifted rectangle produces 5000m2 intersection. Measure the full rectangle as 10000m2. Use GeoPandas/Shapely, real GeoPackage IO, and PROJ forward/inverse transformation; report pass/fail checks and generate WGS84 GeoJSON preview of source/intersection. Save a JSON report. Check actual OpenFileGDB and GeoTIFF driver availability and tiny raster IO if installed. Preserve original data. Probe is synthetic and is not real-world survey accuracy validation.

Phase 1A methods are `source.inspect`, `workspace.get`, `vector.import`, `vector.export`, `task.get`, `task.cancel`, `layer.update`, `layer.reorder`, `layer.remove`, `vector.page`, `vector.viewport`, and `vector.feature`. Exact parameter/result shapes and query limits are in [Phase 1A](../docs/phase-1a.md#shared-rpc-contract) and [contracts.ts](contracts.ts). Every project-dependent request includes the active project path. No frontend method accepts arbitrary SQL or arbitrary managed file paths. Source inspection is read-only and accepts supported local source paths only.

The Python entry point is `python -m spatial_engine` (stdio). `--request '<JSON request>'` runs one request and exits for CI/package smoke tests. The frozen console executable is named spatial-engine.exe and accepts the same switch. The private `--worker <request-file>` mode executes one GIS job in its staging directory, using atomic progress/result files; stdout remains unused by the worker. Project metadata is modified only by the parent service.

Application version 0.6.1. Project schema version 6. The engine stores its rotating log under LOCALAPPDATA/SpatialAnalysis/logs/engine.log (with an appropriate non-Windows development fallback). No log tokens or secrets. Error response: {jsonrpc:"2.0",id,error:{code,message,data?:{kind,detail?}}}. Standard parse/invalid-request/method/params errors use -32700/-32600/-32601/-32602. Domain failures use -32000 with stable data.kind. A syntactically valid JSON value that is not an RPC object is an invalid request; a parser recursion failure is a parse error.

## Phase 1B Methods

- table.inspect, params TableOptions -> TableInspection. Options contain exactly
  sourcePath, encoding, delimiter, sheet and headerRow. XLSX sheet=null enumerates
  sheets; import requires an explicit worksheet. CSV encoding/delimiter are explicit;
  XLSX uses null for both. Preview is bounded and does not validate the full source.
- table.import, params TableOptions plus path -> Task (kind import). Creates an
  immutable TableDataset with records registered as GeoPackage attributes.
- table.page, params {path,datasetId,offset,limit,sortField,descending,filter} ->
  AttributePage. Same bounds as vector.page, restricted to tables.
- table.export, params {path,datasetId,destination} -> Task (kind export). Exports
  a new GeoPackage including auxiliary cell provenance; destination must not exist.
- table.points, params {path,datasetId,xField,yField,declaredCrs} -> Task (kind points).
  Publishes a separate point vector, retains invalid rows with null geometry and
  error fields, and requires at least one valid point. No formulas or CRS guessing.

Table and vector routes validate dataset kind. Workspace.datasets is a discriminated
union; tables have no MapLayer. Detailed preservation and limits: [Phase 1B](../docs/phase-1b.md).

## Phase 1C Methods

- raster.inspect, params {sourcePath} -> RasterInspection. Read-only bounded
  metadata and sampled display statistics for a self-contained local GeoTIFF.
- raster.import, params {path,sourcePath} -> Task. Copy original bytes to an
  immutable managed GeoTIFF and create a raster map layer with a default style.
- raster.export, params {path,datasetId,destination} -> Task. Export the complete
  snapshot to a new .tif/.tiff, with hash validation and no overwrite.
- raster.render, params {path,datasetId,bbox,width,height,style} -> RasterRenderResult.
  Exact EPSG:3857 bbox, at most 1024 x 1024 RGBA PNG, bounded base64 response.
- raster.sample, params {path,datasetId,coordinate} -> RasterSampleResult.
  Input coordinate is EPSG:4326 longitude/latitude; output uses original grid cells,
  raw-value strings and validity. NoData is distinct from valid zero.
- layer.update accepts rasterStyle only for raster layers; vector color/category
  properties are invalid for rasters. Existing vector JSON shapes are preserved.

All data queries reject the wrong dataset kind. Unknown CRS prevents raster map
display and geographic pixel queries. Limits and retained metadata are in
[Phase 1C](../docs/phase-1c.md). Private worker protocol is version 5; raster
operations use snapshot.tif/export.tif and reuse the existing publication journals.

## Phase 1D Methods

- project.saveAs, params {path,directory,name,description,analysisCrs,displayCrs,viewState}
  -> Task, kind save_as. Directory must not exist, its parent must exist, and it
  must be outside the current project. Creates a new project identity using a
  SQLite backup and complete verified registered snapshots. Form values apply
  only to the copy. Dataset JSON/IDs/versions and terminal task/source history
  remain intact. Destination points to the new project.spa. The active session
  remains on the original; the desktop opens the copy after task completion.
- source.status, params {path,datasetId} -> SourceStatus. Reports original and
  currently resolved paths, present/missing/internal/unavailable, relocated and
  verifiedAt. Presence is a filesystem observation, not content verification;
  verifiedAt is a historical successful relocation check. Derived TablePoints
  resolve their parent table by internal ID/version in the current project.
- source.relocate, params {path,datasetId,sourcePath} -> Task, kind relocate.
  Match the immutable import fingerprint before appending a source location.
  Renamed single files/SHP bundles normalize names against the original naming;
  GDB component names remain part of identity. Legacy SHP bundles with different
  component-stem casing support name-preserving moves; fully renamed bundles
  may be unverifiable because individual original names were not recorded.
  Content mismatch requires a new import. This operation never rewrites
  Dataset.source or snapshot bytes.

Save As blocks project/layer mutations for the entire task, including verification
and publication, until it completes or is cancelled.
Task cancellation, publication journals and no-overwrite behavior apply to copies.
Copy budget is 32 GiB; links/escaped paths, missing/changed snapshots and unresolved
publication journals are rejected. Recovery hashes retained artifacts before
publication; conflicts preserve the journal and pending directory for inspection.
Copies keep an ownership marker while the directory is moved to its final name;
the marker prevents opening it until all files pass leased hash verification.
Large recovery uses a cancellable, bounded child. Recovery with at most 1 MiB
of artifacts may run inline with a shared actual 1-MiB hash-read budget.
CART-00 is an isolated development probe with no public renderer method.

## Phase 2 Methods

- analysis.run, params `{path,...AnalysisOptions}` -> Task(kind analysis).
  Exact required fields: operation (clip/intersect), name, inputDatasetId,
  overlayDatasetId, inputClassField, overlayClassField (null for clip),
  classificationStandard, analysisCrs, crsReason. Text fields name/standard/reason
  are bounded to 200 characters; analysisCrs to 8192. Full immutable snapshots
  are used. Positive-area same-layer overlap rejects statistics, except clip
  boundaries which are unioned under a separate boundary budget. No repair.
- analysis.result, params `{path,datasetId,offset,limit}` -> AnalysisResultPage.
  Result must have SpatialAnalysis lineage resolving to the active project's
  input IDs/versions. Statistics are paged, at most 500 rows; record schema 1
  includes CRS/operation/units/versions and both study and coverage denominators.
- analysis.exportCsv, params `{path,datasetId,destination}` -> Task(kind export).
  Destination must be a new .csv. UTF-8 BOM text includes explicit NULL flags
  and unrounded geometric areas. Values remain text, including leading zeros;
  spreadsheet software's automatic type conversion is outside CSV semantics.
  vector.export for the result copies the complete GPKG, including registered
  analysis_record and analysis_statistics attribute tables.

Import/export/analysis/Save As publication verification runs in a second killable
child within the same task deadline. Parent-held Windows read leases deny writes
through verification and publication; proof messages are private and cannot be
provided by public RPC. Journals retain recovery integrity/no-overwrite checks.
Schema 1–5 migrates transactionally to 6 with a verified backup first.

Private --query-worker mode allows only read inspection/display/page/result
operations, with bounded frames, a 2-second operation deadline and 1-GiB budget.
query_unready means asynchronous warmup is incomplete and may be retried;
query_timeout/query_memory_limit recycle that process without closing the project.
Task workers have autonomous 900-second/2-GiB process-tree limits, independent of
task polling. Cancellation allows 2 seconds cooperatively, then terminates the
worker tree. Rust's 90-second deadline includes mutex queue time; queue expiry
alone does not terminate the healthy engine. All limits and formal area policy
are specified in [Phase 2](../docs/phase-2.md).

The Tauri command is engine_request(method: EngineMethod, params: object), returning the result value or a serialized EngineError. The frontend receives only results, not JSON-RPC envelopes. In a standalone browser the native bridge is unavailable and project/engine actions must show this honestly; no fake success or mock persistent projects.
