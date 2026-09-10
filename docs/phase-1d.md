# Phase 1D: workspace portability and consolidation

Status: delivered on 2026-09-10. Local source/frozen/native/distribution acceptance
and Windows CI for pushed feature commit 41fe4b5 passed; see
[verification evidence](verification-phase-1d.md).

## Scope

- Application 0.5.0, public RPC 5, project schema 5, worker protocol 4.
- Save As creates a new directory project containing a consistent SQLite backup,
  every registered immutable vector/table/raster snapshot, layer settings, source
  relocation history, and terminal historical tasks. Cache, staging, lock files,
  and migration backups are not authoritative copy inputs.
- Save As takes the current project form values, including view state. It assigns
  a new project ID; dataset IDs, versions, source provenance and dataset bytes
  remain unchanged. Original project form metadata is not saved implicitly.
- Copying is a cancellable worker task. The active project remains open until
  the copy is fully validated and published. The UI opens the successful copy.
- Destination must be a new directory whose parent exists; refuse an existing
  target, source directory, or descendant of the source project. No overwrites.
  Validate all registered files and SHA-256 identities, reject links/escaped paths,
  reject unresolved publication journals, and check a 32 GiB copy budget.
- Preserve recoverable publication state after failures. An interrupted directory
  must never be accepted or reported as a completed project copy. Never delete
  unrelated target content during cancellation or recovery.
- Source status distinguishes presence from verified identity. Explicit relocation
  hashes a candidate using the import fingerprint policy and only registers an
  identity match. Keep immutable Dataset.source metadata; append a separate
  location record. Changed content requires import as a new dataset.
- TablePoints sources resolve by parentDatasetId/parentVersion inside the active
  project; their original absolute provenance path is not a runtime dependency.
- Opening schema 1-4 projects first creates a SQLite backup and migrates within a
  transaction. Historical dataset JSON and native snapshot bytes are preserved.
- Synthetic mixed-format acceptance covers missing originals, Save As, moved
  project directory, source relocation, re-open, queries, render/sample and export.
- CART-00 is a draft spec and disposable comparison probe only: Chinese text,
  projected polygons with holes/multipart, transparency, and raster input; compare
  OpenLayers offscreen PNG and Python document output. Record dependency,
  full-data, font, vector/raster and frozen-distribution limits. No Layout UI,
  formal cartographic export RPC, Nature compliance claim, or Agent integration.

## Public additions

`project.saveAs`: `{path, directory, name, description, analysisCrs, displayCrs,
viewState}` -> `Task`, kind `save_as`, destination = final `project.spa` path,
datasetId = null. Historical task destinations are provenance, not instructions.

`source.status`: `{path, datasetId}` -> `SourceStatus`:
`{datasetId, originalPath, resolvedPath, availability, relocated, verifiedAt}`.
Availability is `present | missing | internal | unavailable`. A present file is
not a claim that its current bytes match. verifiedAt is a past successful
relocation check or null, never a current integrity guarantee.

`source.relocate`: `{path, datasetId, sourcePath}` -> `Task`, kind `relocate`,
datasetId = selected dataset, destination = candidate source path. Derived
TablePoints datasets reject external relocation. New task kinds retain the
existing status and progress fields. Check exact parameter keys and bounds.

## Acceptance and boundaries

Test copy cancellation, failures before/after publication, existing targets,
missing/corrupt snapshots, source mismatch, renamed/moved source bundles,
internal derivation resolution, schema migration and original data preservation.
Keep real planning data, ArcGIS, independent Windows, offline/upgrade, unsigned
installer and large-scale performance limitations explicit. Evidence goes into
`docs/verification-phase-1d.md`; stage closeout into `执行说明.md` and `TODO.md`.

Historical Shapefile fingerprints include component filenames but do not retain
each original component's casing. Moving a mixed-case bundle while preserving
its filenames is supported. Fully renaming such a legacy bundle may fail strict
identity matching; changed or unverifiable sources must be imported separately.
