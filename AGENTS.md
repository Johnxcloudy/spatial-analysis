# Spatial Analysis Desktop

Windows-first personal GIS workstation for land-use overlay and area statistics.

## Current Scope

The user approved the optimized plan in docs/plan-v2.md and authorized Phase 0 development and Git deployment. Complete Phase 0 only: desktop shell, Python engine communication, project create/save/open, logging, genuine GIS diagnostics, packaging, and verification. The map is a diagnostic projection preview, not a Phase 1 GIS workspace.

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
- Update README, docs, and TODO for delivered behavior and limitations. Stop at the Phase 0 boundary.

## Ownership During Initial Parallel Build

- Engine worker: gis-engine/ and local Python environment only.
- Frontend worker: root npm manifests/config and apps/desktop/ except src-tauri/.
- Primary agent: shared/, apps/desktop/src-tauri/, scripts/, Git, CI, and integration docs.
- Toolchain worker: local Rust installation only.

Use apply_patch for authored files. Do not overwrite another worker's files without coordinating.
