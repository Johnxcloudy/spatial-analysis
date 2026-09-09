# Spatial Analysis Desktop

Windows-first personal GIS workstation for land-use overlay and area statistics.

## Current Scope

Phase 1A (0.2.0) is delivered with synthetic acceptance data; see docs/verification-phase-1a.md. The current request adds cartography and optional Agent design assistance to future tasks, documented in docs/cartography-roadmap.md. It authorizes roadmap updates, not implementation of those future features. Phase 1B coordinate tables, 1C rasters, 1D consolidation and Phase 2 land overlay remain the next product milestones. See docs/phase-1a.md and shared/contracts.ts for the implemented contracts.

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
- Update README, docs, and TODO for delivered behavior and limitations. Keep planned capabilities distinct from implemented contracts; select the authorized milestone before changing application behavior.

## Future Cartography Constraints

- Styling, terrain visualization and layout must not mutate source datasets, analysis geometry, values, units or statistical policies. Derived display products retain their input versions and parameters.
- Manual tools, templates and optional Agent suggestions use one versioned, validated Cartography Spec. Agent suggestions require preview/adoption and undo; they cannot supply arbitrary rendering code or bypass data/resource access controls.
- Formal exports use complete data and explicit output budgets, not the bounded interactive viewport. Validate map-frame CRS, physical scale, legends, NoData, fonts and vector/raster output capabilities.
- Publication/Nature themes are configurable presets with rule provenance, not claims of journal or planning compliance. Offline manual rendering remains available without an Agent.

## Ownership During Phase 1A

- Store worker: projects.py, workspace.py, tasks.py, worker.py and related tests only.
- Vector worker: vectors.py, vector_queries.py, pyproject.toml/uv.lock, GIS fixtures and related tests only.
- Frontend worker: apps/desktop/src/ only.
- Primary agent: shared/, apps/desktop/src-tauri/, scripts/, Git, CI, and integration docs.
- Primary also owns RPC routing, entry point, version coordination and end-to-end acceptance scripts.

Use apply_patch for authored files. Do not overwrite another worker's files without coordinating.
