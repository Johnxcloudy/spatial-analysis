# Spatial Analysis Desktop

Windows-first personal GIS workstation for land-use overlay and area statistics.

## Current Scope

Phase3B / 0.6.2 implementation and local source/frozen/native/install acceptance are
complete; both current functional Windows CI runs passed. Read docs/phase-3b.md,
docs/verification-phase-3b.md and the current execution handoff before selecting work.
Query parent deadline/startup cleanup and retired monitor enforcement are fixed;
opt-in host timings and bounded CI diagnostics are delivered in the internal release.
Protocol/schema6, worker5 and readiness1 are unchanged. Functional commit
b3c7100fa81e8fa46c3e1a1ffd3211e47c3657d8 is pushed on feat/phase-1a. Push34445680269
and PR34445683926 succeeded, 32 visible steps each. Public notices verify checkout
b3c7100fa81e8fa46c3e1a1ffd3211e47c3657d8 for push and merge snapshot
f8d8e1c046cb1420fa32bb1a6b504f815914e8f0 for PR, both326 pytest cases with zero failures.
Documentation closeout is a separate [skip ci] commit; inspect actual HEAD/remote.
Production ownership has returned to root; UI/native/install processes are cleaned up.
Reassign explicit ownership before future work; shared/contracts.ts stays root-owned.
Current 100k/250k/500k pagination reuses synthetic snapshots, not a new complete500k
analysis or OS cold-cache test. Native timing correlates by time without shared IDs;
the old3998ms event and original PR failure remain unexplained. Preserve those limits.

### Phase3A local delivery and unresolved history

Phase 3A / 0.6.1 implementation and local source/frozen/native/install acceptance are complete; remote acceptance remains incomplete because PR CI failed at Test engine. Protocol/schema 6, worker 5 and readiness 1 are unchanged. Read docs/phase-3a.md, docs/verification-phase-3a.md and the current 执行说明.md handoff. Functional commit 4db4999c5075123e5e99b2853130864d7b0621ab is pushed. Track push 34441137906 and PR 34441140400 separately; do not infer the failed test or claim all CI passed. Public browser requires sign-in for step logs. Preserve this unresolved gate before selecting a new milestone.

Delivered locally: verified default pagination, explicit retry, and real-data import/export/project preservation. The supplied test/ and all derived diagnostic artifacts stay out of Git; originals are read-only. The sample has 127 polygons, one invalid geometry, and lacks classified analysis inputs. Do not infer classification, repair policy or business results. Current 100k/250k/500k frozen pagination uses existing synthetic fixtures; per-page proof still scans all rows and measurements do not represent OS cold cache. Phase 3A does not complete all Phase 3 external acceptance. Production ownership has returned to root; reassign explicitly before further edits.

### Historical Phase 2 delivery

Phase 2 (0.6.0) is delivered as an internal trial release: land overlay/area statistics for the user's typical 100000–500000 parcels. Public protocol/schema 6, worker 5, readiness 1. Local synthetic source regression, final frozen five-level acceptance, full 500000 native UI run, distribution/install and Windows CI passed. Feature commit 9258d7859ef3c832bfd481be266df2e2250ddcf5 is pushed; Windows CI 34434218036 succeeded (29 visible steps). Closeout scripts/docs use a separate [skip ci] commit located by subject "chore: close Phase 2 delivery"; do not apply feature CI to unverified later code. Read docs/phase-2.md, docs/verification-phase-2.md and docs/superpowers/plans/2026-09-10-phase-2.md.

Phase 2's historical pagination/retry follow-up is implemented in Phase 3A; true cold-read acceptance remains open. The historical unfiltered vector.page timeout is not explained solely by EXPLAIN. Bounded response is not a promise that all queries succeed. Real formal planning analysis, ArcGIS comparison, independent Windows and offline/upgrade acceptance remain open. Formal cartography and Agent assistance remain future work. Read the latest user instruction and 执行说明.md before selecting the next milestone; shared/contracts.ts owns public contracts.

Phase 2 worker assignments are closed; establish new explicit ownership before parallel edits. Native acceptance processes and Vite were cleaned up at closeout; recheck transient process state on resume. Historical Phase 1D (0.5.0) acceptance/CI remains separate evidence.

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

## Delivered Phase 1C

Application 0.4.0, public protocol 4, project schema 4, private worker protocol 3.
GeoTIFF snapshots are native rasters/<UUID>.tif files; display PNGs never replace
the original grid. Source/frozen/native/install checks and Windows CI passed;
real-data, ArcGIS, independent Windows, offline/upgrade and performance checks
remain open. See 执行说明.md for exact commits and evidence boundaries.

Phase 1C worker assignments are closed. Establish explicit file ownership for
future parallel work. Use apply_patch and coordinate edits to shared files.

## Phase 1D Delivery

Save As preserves all registered snapshots, dataset identities, layer settings,
terminal history and current form values under a new project identity. Source
relocation appends verified location history without modifying provenance.
Source presence is not current content verification; TablePoints resolve their
parent inside the active project. Copy budget is 32 GiB and targets cannot exist.
Historical mixed-case SHP bundles support name-preserving moves; full renaming
may be unverifiable. Source/frozen/native/install acceptance passed locally.
CART-00 is an isolated experiment, not a production renderer or Agent feature.
Read 执行说明.md for final Git/CI state and outstanding acceptance boundaries.
Phase 1D implementation worker assignments are closed; reassign ownership for
future changes rather than assuming historical ownership remains active.
