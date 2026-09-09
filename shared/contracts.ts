export const PROTOCOL_VERSION = 3 as const;

export interface ViewState {
  center: [number, number];
  zoom: number;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  createdAt: string;
  updatedAt: string;
  schemaVersion: 3;
  projectPath: string;
  analysisCrs: string | null;
  displayCrs: string;
  viewState: ViewState;
}

export interface RuntimeInfo {
  protocolVersion: 3;
  engineVersion: string;
  pythonVersion: string;
  packaged: boolean;
  versions: Record<string, string>;
  drivers: Record<string, string>;
  logPath: string;
}

export interface DiagnosticCheck {
  id: string;
  label: string;
  passed: boolean;
  detail: string;
}

export interface ProbeFeature {
  type: "Feature";
  properties: { name: string; role: "source" | "intersection" };
  geometry: { type: "Polygon"; coordinates: number[][][] };
}

export interface ProbeReport {
  ok: boolean;
  crs: string;
  expectedAreaM2: number;
  measuredAreaM2: number;
  expectedIntersectionAreaM2: number;
  intersectionAreaM2: number;
  areaErrorM2: number;
  roundTripErrorM: number;
  checks: DiagnosticCheck[];
  preview: { type: "FeatureCollection"; features: ProbeFeature[] };
  sourceBounds: [number, number, number, number];
  versions: Record<string, string>;
  reportPath: string;
  geopackagePath: string;
  durationMs: number;
}

export interface EngineError {
  code: number;
  message: string;
  data?: { kind: string; detail?: string };
}

export type EngineMethod =
  | "runtime.info"
  | "project.create"
  | "project.open"
  | "project.save"
  | "project.close"
  | "diagnostics.run"
  | "source.inspect"
  | "workspace.get"
  | "vector.import"
  | "vector.export"
  | "table.inspect"
  | "table.import"
  | "table.export"
  | "table.page"
  | "table.points"
  | "task.get"
  | "task.cancel"
  | "layer.update"
  | "layer.reorder"
  | "layer.remove"
  | "vector.page"
  | "vector.viewport"
  | "vector.feature";

export type Bounds = [number, number, number, number];
export type FieldValue = string | number | boolean | null;

export interface VectorField {
  name: string;
  sourceType: string;
  storageType: string;
  nullable: boolean | null;
  alias: string | null;
  width: number | null;
  precision: number | null;
  metadataStatus: "not_read" | "partial";
}

export interface SourceLayer {
  name: string;
  geometryType: string | null;
  featureCount: number | null;
  crsWkt: string | null;
  crsAuthority: string | null;
  fields: VectorField[];
  bounds: Bounds | null;
}

export interface SourceInspection {
  sourcePath: string;
  driver: string;
  layers: SourceLayer[];
  warnings: string[];
}

export interface ImportReport {
  status: "warning" | "restricted";
  checks: { code: string; passed: boolean; detail: string; count?: number }[];
  warnings: string[];
  notChecked: string[];
  counts: Record<string, number>;
  validatorVersion: string;
}

export interface DatasetCommon {
  id: string;
  version: string;
  name: string;
  source: {
    path: string;
    layer: string;
    driver: string;
    fingerprint: string;
    encoding: string | null;
    assignedCrs: string | null;
    crsWkt: string | null;
    metadata: Record<string, string>;
  };
  relativePath: string;
  storageLayer: string;
  featureCount: number;
  crsWkt: string | null;
  crsAuthority: string | null;
  bounds: Bounds | null;
  boundsWgs84: Bounds | null;
  fields: VectorField[];
  internalIdField: string;
  sourceFidField: string;
  report: ImportReport;
  createdAt: string;
}

export interface VectorDataset extends DatasetCommon {
  kind: "vector";
  geometryType: string;
}

export interface TableDataset extends DatasetCommon {
  kind: "table";
  geometryType: null;
  crsWkt: null;
  crsAuthority: null;
  bounds: null;
  boundsWgs84: null;
  storageLayer: "records";
  cellMetadataLayer: "cell_metadata" | null;
}

export type Dataset = VectorDataset | TableDataset;

export interface TableOptions {
  sourcePath: string;
  encoding: string | null;
  delimiter: string | null;
  sheet: string | null;
  headerRow: number;
}

export interface TableInspection {
  sourcePath: string;
  driver: "CSV" | "XLSX";
  sheets: string[];
  sheet: string | null;
  columns: { index: number; sourceName: string | null; fieldName: string }[];
  rows: { sourceRow: number; values: Record<string, string | null> }[];
  truncated: boolean;
  warnings: string[];
}

export interface MapLayer {
  id: string;
  datasetId: string;
  name: string;
  visible: boolean;
  opacity: number;
  color: string;
  categoryField: string | null;
  categoryColors: Record<string, string>;
  order: number;
}

export interface Task {
  id: string;
  kind: "import" | "export" | "points";
  status: "running" | "completed" | "failed" | "cancelled" | "interrupted";
  stage: string;
  completed: number | null;
  total: number | null;
  createdAt: string;
  updatedAt: string;
  datasetId: string | null;
  destination: string | null;
  error: string | null;
}

export interface Workspace {
  projectId: string;
  datasets: Dataset[];
  layers: MapLayer[];
  tasks: Task[];
}

export interface AttributeFilter {
  field: string;
  operator: "contains" | "equals" | "isNull";
  value: string;
}

export interface AttributeRow {
  id: string;
  sourceRow?: string | null;
  values: Record<string, FieldValue>;
}

export interface AttributePage {
  datasetId: string;
  version: string;
  fields: VectorField[];
  rows: AttributeRow[];
  total: number;
  offset: number;
  limit: number;
  hasMore: boolean;
  truncated: boolean;
}

export interface DisplayFeature {
  type: "Feature";
  id: string;
  properties: Record<string, FieldValue>;
  geometry: { type: string; coordinates?: unknown; geometries?: unknown[] } | null;
}

export interface ViewportResult {
  datasetId: string;
  version: string;
  bbox: Bounds;
  dataCrs: "EPSG:4326";
  collection: { type: "FeatureCollection"; features: DisplayFeature[] };
  truncated: boolean;
  returnedCount: number;
}

export interface FeatureResult {
  datasetId: string;
  version: string;
  row: AttributeRow;
  feature: DisplayFeature | null;
  boundsWgs84: Bounds | null;
}
