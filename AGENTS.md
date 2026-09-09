# Spatial Analysis Desktop

Windows-first personal GIS workstation for land-use overlay and area statistics.

## Current Scope

The user approved docs/plan-v2.md and has now authorized Phase 1A development and Git deployment. Deliver ordinary GPKG/SHP/GeoJSON/GDB vectors, managed snapshots, map/layers/attributes/export, cancellable import jobs and schema migration. Use synthetic acceptance data as requested. Phase 1B coordinate tables, 1C rasters, 1D consolidation and Phase 2 land overlay remain subsequent milestones. See docs/phase-1a.md and shared/contracts.ts for the current contracts.

## Engineering Rules

- Read docs/plan-v2.md and docs/gis-data-standard.md for the product and reliability requirements.
- React handles interaction; Python performs authoritative GIS computation and owns project storage; Tauri controls native access and process lifetime.
- Preserve original inputs. Do not fabricate data quality, CRS, precision, progress, analysis results, or test evidence.
- Separate display CRS, source CRS, and analysis CRS. Formal area calculations require a documented suitable projected CRS.
- Use mature GDAL/PROJ/Shapely APIs. A SQLite file is not automatically a GeoPackage.
- Persist versioned project metadata and use transactions. Do not overwrite an existing project when creating a new one.
- Never commit credentials, local projects, build output, virtual environments, or diagnostic artifacts.
- Keep RPC versioned, parameter-validated and bounded. Protocol data goes to stdout; logs go to stderr/files.
- Test project round trips, invalid/corrupt inputs, coordinate transformation and genuine GeoPackage IO. Report clean-machine verification as pending until actually run there.
- Work in the existing feature branch; preserve remote history and user documents. Never force-push.
- Update README, docs, and TODO for delivered behavior and limitations. Stop at the Phase 1A boundary.

## Ownership During Phase 1A

- Store worker: projects.py, workspace.py, tasks.py, worker.py and related tests only.
- Vector worker: vectors.py, vector_queries.py, pyproject.toml/uv.lock, GIS fixtures and related tests only.
- Frontend worker: apps/desktop/src/ only.
- Primary agent: shared/, apps/desktop/src-tauri/, scripts/, Git, CI, and integration docs.
- Primary also owns RPC routing, entry point, version coordination and end-to-end acceptance scripts.

Use apply_patch for authored files. Do not overwrite another worker's files without coordinating.
