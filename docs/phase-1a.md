# Phase 1A Implementation Plan

**Goal:** Import selected ordinary 2D GPKG, SHP, GeoJSON and OpenFileGDB feature classes; inspect, display, query, style, export and reopen their managed snapshots.

**Architecture:** Python owns immutable per-dataset GeoPackages and project schema v2. GIS jobs run in a child process and publish only validated artifacts. React/OpenLayers reads bounded display features and attribute pages through the native host.

**Tech Stack:** Existing Tauri 2 / React / OpenLayers / Python stack, adding pinned PyArrow to preserve nullable 64-bit integers.

## Constraints and Acceptance

- User authorized implementation and Git deployment; synthetic data is the requested first acceptance dataset. No further design approval gate.
- Application 0.2.0, protocol 2, project schema 2. Phase 0 projects are backed up using SQLite backup before transactional migration. Future schemas remain rejected.
- Inputs remain untouched. Dataset version, source fingerprint/FID, field mapping, complete CRS and conversion report persist. Advanced GDB metadata unavailable through the API is explicitly not read.
- Unknown CRS is attributes-only unless explicitly declared during import. Existing CRS cannot be overridden. 3D/measured/curved geometry that cannot be preserved is rejected. Invalid/empty geometries are retained with restricted quality status, never silently repaired.
- Initial hard limits: 100,000 input features, 2,000,000 total vertices, 256 fields, 512 source layers, 64 managed datasets per project, 256 KiB metadata per dataset and 6 MiB workspace response. Import scans in batches. Queries: 200 rows default / 500 maximum, 2,000 display features, 100,000 display vertices, 2 MiB serialized query result. Exceeded budgets produce a clear error or explicit truncated result, never a false complete result.
- Map display is EPSG:3857 using WGS84 response coordinates. Analysis CRS remains separate. No formal area or overlay workflow in this milestone.
- Cancellation, worker failure and interrupted application shutdown do not register partial results. One active job per project; close/switch cancels active work before releasing the project lock. Interrupted persisted jobs become interrupted on reopen; no automatic resume.
- Ordinary export is a new verified GeoPackage, with immutable IDs/attributes/geometry and the original snapshot CRS. Destination must not exist. This does not claim GDB advanced semantic preservation.
- Acceptance covers GDB multiple classes, GPKG multiple layers, SHP components and Chinese encoding, leading zeros, NULL/int64, sparse FIDs, invalid/empty/hole/multipart geometry, unknown/CGCS2000 CRS, refusal of Z/M/curves, deterministic sort/filter/pages, bounded transformed viewport, map/table selection, cancellation, migration, reopen after source move, frozen worker and native UI.

## Shared RPC Contract

All project-dependent requests include `path`, which must match the active project. Results/types are in `shared/contracts.ts`.

| Method | Parameters (in addition to path where applicable) | Result |
| --- | --- | --- |
| source.inspect | sourcePath, encoding: string or null; no project path | SourceInspection |
| workspace.get | none | Workspace |
| vector.import | sourcePath, sourceLayer, encoding: string or null, assignedCrs: string or null | Task |
| vector.export | datasetId, destination | Task |
| task.get / task.cancel | taskId | Task |
| layer.update | layerId, changes: subset of name/visible/opacity/color/categoryField/categoryColors | MapLayer |
| layer.reorder | layerIds: all existing IDs in display order (first is top) | MapLayer[] |
| layer.remove | layerId | {removed:true}; retains Dataset snapshot |
| vector.page | datasetId, offset, limit, sortField: string or null, descending: boolean, filter: AttributeFilter or null | AttributePage |
| vector.viewport | datasetId, bbox: WGS84 bounds, limit <= 2000, propertyFields: string[] (max 3) | ViewportResult |
| vector.feature | datasetId, featureId: string | FeatureResult |

Field names are validated against stored metadata; no caller SQL accepted. IDs always cross IPC as strings. Every data query identifies the immutable dataset version. `workspace.get` and `task.get` harvest finished workers before responding. Layer edits persist immediately; map view is saved with project metadata. Frontend query responses are discarded when project, dataset, selection or query identity changes.

## Python Module Boundaries

- `ProjectStore.active_path(path: str) -> Path` checks session identity. Create/open returns the existing Project shape with schemaVersion 2.
- `WorkspaceStore(projects)` implements `get(params)`, `dataset(path, dataset_id) -> dict`, `managed_path(dataset) -> Path`, `update_layer(params)`, `reorder_layers(params)`, `remove_layer(params)`, plus task/publish persistence helpers owned by its implementer.
- `TaskManager(projects, workspace)` implements `start_import(params)`, `start_export(params)`, `get(params)`, `cancel(params)`, `harvest()`, `close()` (cancel/reap active process). Root calls close before project switch/close and harvest before workspace.get.
- `vectors.inspect_source(params) -> SourceInspection`.
- `vectors.import_vector(payload, work_dir: Path, progress, cancelled) -> {dataset: VectorDataset, artifactPath: str}`. Payload contains sourcePath/sourceLayer/encoding/assignedCrs/datasetId. Write `work_dir/snapshot.gpkg`, storage layer `features`. Dataset relativePath is `datasets/<datasetId>.gpkg`; version is the verified snapshot content SHA256. Progress callback `(stage: str, completed: int|None, total: int|None)`. Cancel callback returns bool; raise DomainError kind task_cancelled on cancellation.
- `vectors.export_vector(payload, work_dir: Path, progress, cancelled) -> {artifactPath: str}`. Payload contains dataset and managedPath; validate before/after copy to `work_dir/export.gpkg`. Parent owns destination publication and must not overwrite existing files.
- `vector_queries.attribute_page(dataset, managed_path: Path, params)`, `viewport(dataset, managed_path, params)`, `feature(dataset, managed_path, params)` produce shared result types. Root obtains dataset only through WorkspaceStore.
- `worker.run_worker(request_path: Path) -> int` owns task request/progress/result/error JSON files; root entry point handles `--worker <request_path>` before constructing Engine. Same frozen executable is used in worker mode.

## Delivery Tasks

- [x] Freeze scope/contracts and assign independent file ownership.
- [x] Store worker: test v1 backup/migration and immutable registration, then implement ProjectStore schema v2, WorkspaceStore, TaskManager and worker mode; verify rollback, cancel and interrupted-job recovery.
- [x] Vector worker: install pinned Arrow, test field/geometry fidelity and format fixtures, implement source inspection/import/export and bounded query services, then run focused Python tests.
- [x] UI worker: implement import selection, task monitor, map/layers, pagination/filter/sort, selection linkage, metadata/report and export. Preserve project drafts across refresh, enforce recovery state, reject stale query responses, test interaction and persistence.
- [x] Root: fix CI robustness assertion, wire RPC/host/version changes, review integration and run all test suites.
- [x] Root: run source/frozen/native acceptance with synthetic fixtures, desktop/mobile screenshots and map pixel checks; build and smoke installer. Record unperformed clean-machine/ArcGIS/real-data checks honestly.
- [x] Root: update README/TODO/verification with delivered behavior and local acceptance evidence.

Git delivery uses `feat/phase-1a` without rewriting remote history. The matching remote commit and Windows CI run provide the deployment status; local acceptance is recorded in [verification-phase-1a.md](verification-phase-1a.md).
