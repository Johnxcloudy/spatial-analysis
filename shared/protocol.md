# Phase 0 engine protocol

Transport: persistent console sidecar, UTF-8 newline-delimited JSON-RPC 2.0. The host assigns request IDs and serializes calls. One response line per request. No protocol chatter or logs on stdout. A host deadline terminates an unresponsive process. Methods not listed here are rejected. Responses use camelCase matching shared/contracts.ts. Protocol version is 1.

## Methods

- runtime.info, params {} -> RuntimeInfo. Load actual GIS libraries and report actual GDAL driver capabilities, not assumed availability.
- project.create, params {directory: string, name: string} -> Project. directory is the intended NEW project directory (may exist if project.spa does not). name is a nonempty display name. Create project.spa and owned subdirectories; never overwrite a project. Default description empty, analysisCrs null, displayCrs EPSG:3857, viewState {center:[114,27.1],zoom:5}. Engine holds a lock for the active project. Creation/open closes the previously active project only after the new operation succeeds.
- project.open, params {path: string} -> Project. path is an existing project.spa. Validate identity and schema before any writes; acquire an exclusive OS-backed lock, fail clearly if held by another engine. Reject future schema versions and corrupt files.
- project.save, params {path:string, name:string, description:string, analysisCrs:string|null, displayCrs:string, viewState:{center:[number,number],zoom:number}} -> Project. Must refer to the active project. Validate finite coordinates, zoom and CRS. Use an atomic SQLite transaction; preserve id/createdAt and update updatedAt. Invalid inputs must not change the project.
- project.close, params {} -> {closed:true}. Release active project lock.
- diagnostics.run, params {directory:string} -> ProbeReport. directory is for generated diagnostic artifacts (a cache folder, not original inputs). Create a unique subdirectory per run. Test a 100m x 100m rectangle at x=500000, y=3000000 in EPSG:4547; overlay with a 50m-shifted rectangle produces 5000m2 intersection. Measure the full rectangle as 10000m2. Use GeoPandas/Shapely, real GeoPackage IO, and PROJ forward/inverse transformation; report pass/fail checks and generate WGS84 GeoJSON preview of source/intersection. Save a JSON report. Check actual OpenFileGDB and GeoTIFF driver availability and tiny raster IO if installed. Preserve original data. Probe is synthetic and is not real-world survey accuracy validation.

The Python entry point is `python -m spatial_engine` (stdio). `--request '<JSON request>'` runs one request and exits for CI/package smoke tests. The frozen console executable is named spatial-engine.exe and accepts the same switch.

Application version 0.1.0. Project schema version 1. The engine stores its rotating log under LOCALAPPDATA/SpatialAnalysis/logs/engine.log (with an appropriate non-Windows development fallback). No log tokens or secrets. Error response: {jsonrpc:"2.0",id,error:{code,message,data?:{kind,detail?}}}. Standard parse/method/params errors use -32700/-32601/-32602. Domain failures use -32000 with stable data.kind.

The Tauri command is engine_request(method: EngineMethod, params: object), returning the result value or a serialized EngineError. The frontend receives only results, not JSON-RPC envelopes. In a standalone browser the native bridge is unavailable and project/engine actions must show this honestly; no fake success or mock persistent projects.
