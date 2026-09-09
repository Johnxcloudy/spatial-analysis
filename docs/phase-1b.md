# Phase 1B: Coordinate Tables

Status: delivered; local verification and functional-commit Windows CI passed.
Application 0.3.0, protocol 3,
project schema 3. Acceptance uses synthetic CSV/XLSX fixtures; distribution
and Git/CI evidence are recorded in verification-phase-1b.md.

## Data Contract

- CSV and XLSX import creates an immutable, nonspatial GeoPackage TableDataset.
  The common featureCount property counts rows. Tables have no map layer or CRS.
- CSV uses explicit encoding, delimiter and one-based header row. Strict decoding
  and row-width checks apply; strings, leading zeros, empty strings and literal
  NULL are retained. No sampled type inference or delimiter/CRS guessing.
- XLSX uses openpyxl in read-only mode with formula text retained. Sheet and
  one-based header row are explicit. Main values are normalized text or NULL;
  sparse cell_metadata records retain non-string scalar types and number formats.
  Formula/error/date/Boolean cells remain distinguishable. Formulas are not run.
  Workbook formatting, layout and original numeric XML representations are not
  promised. Legacy XLS and macro-enabled XLSM are unsupported.
- Source fingerprints, parser options, header mappings and source row identity
  survive import. Internal names are collision-safe. Raw inputs remain untouched.
- Table and derived-point attribute rows expose sourceRow separately from the
  internal ID. CSV source rows count logical records; XLSX rows are worksheet rows.
- Initial limits: 100,000 data rows, 256 total fields, 2,000,000 source cells,
  128 MiB normalized values. Bound source/expanded ZIP size, member count and
  preview payload separately. Preview is partial, not full validation.
  Import allows at most 254 source columns; point derivation allows at most 252.
- CSV encodings: UTF-8/UTF-8 with BOM, GBK and GB18030. Delimiters: comma,
  semicolon, tab or pipe. Header row: 1-1000. Preview: at most 20 rows/1 MiB;
  source: 128 MiB; XLSX expanded members: 256 MiB/10,000 entries/512 sheets.

## Point Derivation

- A table.points background task creates a separate VectorDataset. Parameters:
  path, datasetId, xField, yField, declaredCrs. Parent ID/version and parameters
  are retained in provenance. The parent table remains available.
- X is longitude/easting; Y is latitude/northing. Require an explicit supported
  two-dimensional geographic or projected CRS, finite numbers and valid ranges.
  Only coordinate parsing trims surrounding whitespace; no locale guessing.
- Formula, error, date/time and Boolean coordinate cells are rejected. Invalid
  rows retain attributes and source row identity with NULL geometry and explicit
  status/reason fields. At least one valid point is required.
- Geographic display transformations do not replace source coordinates or make
  the result suitable for formal area calculations.

## Interfaces and Persistence

- Shared definitions in shared/contracts.ts are authoritative. table.inspect
  accepts TableOptions; table.import adds path and returns Task. table.page
  uses the existing attribute-page contract. table.export mirrors vector.export
  and copies the whole snapshot including cell metadata to a new GPKG file.
- Source tables appear in the workspace with attribute paging, filtering,
  sorting, quality report, export and point generation. Only vectors are mapped.
- Reuse worker cancellation, immutable publication and recovery journals. Schema
  1/2 projects migrate with a pre-migration SQLite backup; preserve all datasets,
  layer IDs, task history and project metadata.

## Acceptance

- Test CSV encoding, delimiters, duplicate/blank headers, Unicode, leading zeros,
  mixed cells, XLSX sheets/formulas/errors/dates/number formats, malformed inputs
  and resource bounds. Verify actual GeoPackage attributes registration.
- Test valid/invalid point conversion, explicit CRS, source identity, provenance,
  cancellation, migration and publication recovery with tables and vectors.
- Regress existing tests, verify source/frozen table workflows and native UI,
  build and test the 0.3.0 installer, then commit/push and check that commit's CI.
- Record factual evidence and outstanding real-data/independent-machine checks
  in verification docs and the root execution handoff before stage completion.
