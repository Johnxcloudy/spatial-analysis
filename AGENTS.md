# Spatial Analysis Desktop

Windows-first personal GIS workstation for land-use overlay and area statistics.

## Current Scope

Phase 1B (0.3.0) adds CSV/XLSX tables and explicit coordinate-to-point generation to Phase 1A vectors. Acceptance uses synthetic data; current evidence is in docs/verification-phase-1b.md. Cartography and optional Agent design assistance remain future tasks in docs/cartography-roadmap.md. Phase 1C rasters, 1D consolidation and Phase 2 land overlay are the next product milestones. Read the latest user instruction and the handoff summary in 执行说明.md before selecting work. See docs/phase-1a.md, docs/phase-1b.md and shared/contracts.ts for contracts.

## Required Stage Closeout

- After every stage, append a factual execution summary to the repository-root 执行说明.md before announcing completion. Include scope/status, delivered work and files, decisions, actual verification evidence, unresolved issues, artifacts, known Git/CI state and prioritized next steps.
- Refresh its top handoff summary (normally no more than 30 lines) and TODO.md. Keep detailed logs in linked evidence files; preserve historical stage records and distinguish past tests from newly executed checks.
- Preserve the latest user constraints, approvals, unfinished tasks, dependencies and recovery steps. Do not treat a future roadmap item as already authorized implementation or mark pending verification as passed.
- After saving the records, invoke context compaction if the host exposes a supported capability. If none is available, explicitly say so and use the saved handoff for recovery; never claim the actual conversation was compacted.
- On a resumed or compacted session, read the latest user instruction, this file, 执行说明.md's current handoff and TODO.md first. Recheck HEAD, worktree, remote/CI and active processes before relying on recorded transient state.

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

## Delivered Phase 1B

Phase 1B implementation and local distribution verification are complete. See docs/phase-1b.md,
docs/verification-phase-1b.md and shared/contracts.ts. Versions: application 0.3.0,
protocol 3, project schema 3. Use synthetic data; read the latest Git/CI state in 执行说明.md.

## Ownership During Phase 1B

- Store worker: projects.py, workspace.py, tasks.py, worker.py and related tests only.
- GIS worker: tables.py, vectors.py, vector_queries.py, pyproject.toml/uv.lock, GIS fixtures and related tests only.
- Frontend worker: apps/desktop/src/ only.
- Primary agent: shared/, apps/desktop/src-tauri/, scripts/, Git, CI, and integration docs.
- Primary also owns RPC routing, entry point, version coordination and end-to-end acceptance scripts.

Use apply_patch for authored files. Do not overwrite another worker's files without coordinating.
