export const PROTOCOL_VERSION = 1 as const;

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
  schemaVersion: 1;
  projectPath: string;
  analysisCrs: string | null;
  displayCrs: string;
  viewState: ViewState;
}

export interface RuntimeInfo {
  protocolVersion: 1;
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
  | "diagnostics.run";
