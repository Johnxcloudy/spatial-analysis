import { invoke, isTauri } from '@tauri-apps/api/core';
import { appCacheDir, dirname, join } from '@tauri-apps/api/path';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { open, save } from '@tauri-apps/plugin-dialog';
import type { AnalysisResultPage, AttributePage, Dataset, EngineError, EngineMethod, FeatureResult, MapLayer, ProbeReport, Project, RasterInspection, RasterRenderResult, RasterSampleResult, RuntimeInfo, SourceInspection, SourceStatus, TableInspection, Task, ViewportResult, Workspace } from '../../../shared/contracts';
import { createRequestQueue } from './request-queue';
import { retryQueryWarmup } from './query-warmup';

const requests = createRequestQueue();
const displayMethods = new Set<EngineMethod>(['vector.page', 'vector.viewport', 'vector.feature', 'table.page', 'raster.render', 'raster.sample', 'analysis.result']);

interface EngineResults {
  'analysis.run': Task;
  'analysis.result': AnalysisResultPage;
  'analysis.exportCsv': Task;
  'runtime.info': RuntimeInfo;
  'project.create': Project;
  'project.open': Project;
  'project.save': Project;
  'project.saveAs': Task;
  'project.close': { closed: true };
  'diagnostics.run': ProbeReport;
  'source.inspect': SourceInspection;
  'source.status': SourceStatus;
  'source.relocate': Task;
  'workspace.get': Workspace;
  'vector.import': Task;
  'vector.export': Task;
  'table.inspect': TableInspection;
  'table.import': Task;
  'table.export': Task;
  'table.points': Task;
  'table.page': AttributePage;
  'raster.inspect': RasterInspection;
  'raster.import': Task;
  'raster.export': Task;
  'raster.render': RasterRenderResult;
  'raster.sample': RasterSampleResult;
  'task.get': Task;
  'task.cancel': Task;
  'layer.update': MapLayer;
  'layer.reorder': MapLayer[];
  'layer.remove': { removed: true };
  'vector.page': AttributePage;
  'vector.viewport': ViewportResult;
  'vector.feature': FeatureResult;
}

export function normalizeError(error: unknown): EngineError {
  if (typeof error === 'object' && error !== null && 'message' in error) {
    const candidate = error as Partial<EngineError>;
    return {
      code: typeof candidate.code === 'number' ? candidate.code : -32000,
      message: String(candidate.message),
      data: candidate.data,
    };
  }
  if (typeof error === 'string') {
    try {
      return normalizeError(JSON.parse(error));
    } catch {
      return { code: -32000, message: error };
    }
  }
  return { code: -32000, message: '操作未完成，请查看引擎日志。' };
}

export async function engineRequest<M extends EngineMethod>(
  method: M,
  params: Record<string, unknown> = {},
  signal?: AbortSignal,
): Promise<EngineResults[M]> {
  if (!isTauri()) {
    throw { code: -32000, message: '浏览器模式下本地引擎不可用，请使用桌面应用。', data: { kind: 'NATIVE_UNAVAILABLE' } } satisfies EngineError;
  }
  try {
    return await retryQueryWarmup(() => requests.run(method === 'task.cancel' ? 'urgent' : displayMethods.has(method) ? 'display' : 'control', () => {
      if (signal?.aborted) throw new DOMException('查询已取消', 'AbortError');
      return invoke<EngineResults[M]>('engine_request', { method, params }).catch((cause) => { throw normalizeError(cause); });
    }), signal);
  } catch (error) {
    throw normalizeError(error);
  }
}

export const desktop = {
  available: isTauri,
  request: engineRequest,
  chooseParent: () => open({ directory: true, multiple: false, title: '选择新项目的父目录' }),
  chooseProject: () => open({ multiple: false, directory: false, title: '打开项目', filters: [{ name: 'Spatial Analysis 项目', extensions: ['spa'] }] }),
  chooseVector: () => open({ multiple: false, directory: false, title: '导入矢量数据', filters: [{ name: '矢量数据', extensions: ['gpkg', 'shp', 'geojson', 'json'] }] }),
  chooseGdb: () => open({ multiple: false, directory: true, title: '选择 File Geodatabase (.gdb)' }),
  selectTableSource: () => open({ multiple: false, directory: false, title: '导入坐标表', filters: [{ name: '坐标表', extensions: ['csv', 'xlsx'] }] }),
  chooseRaster: () => open({ multiple: false, directory: false, title: '导入 GeoTIFF', filters: [{ name: 'GeoTIFF', extensions: ['tif', 'tiff'] }] }),
  chooseSource: (dataset: Dataset) => {
    if (['TablePoints', 'SpatialAnalysis'].includes(dataset.source.driver)) return Promise.resolve(null);
    if (['OpenFileGDB', 'FileGDB'].includes(dataset.source.driver)) return open({ multiple: false, directory: true, title: '重新定位来源 File Geodatabase (.gdb)' });
    const extensions = dataset.kind === 'raster' ? ['tif', 'tiff']
      : dataset.source.driver === 'CSV' ? ['csv']
      : dataset.source.driver === 'XLSX' ? ['xlsx']
      : dataset.source.driver === 'ESRI Shapefile' ? ['shp']
      : dataset.source.driver === 'GPKG' ? ['gpkg'] : ['geojson', 'json'];
    return open({ multiple: false, directory: false, title: '重新定位来源', filters: [{ name: dataset.source.driver, extensions }] });
  },
  chooseRasterExport: (name: string) => save({ title: '导出 GeoTIFF', defaultPath: `${name.replace(/[<>:"/\\|?*]/g, '_')}.tif`, filters: [{ name: 'GeoTIFF', extensions: ['tif', 'tiff'] }] }),
  chooseCsvExport: (name: string) => save({ title: '导出分类统计 CSV', defaultPath: `${name.replace(/[<>:"/\\|?*]/g, '_')}.csv`, filters: [{ name: 'CSV', extensions: ['csv'] }] }),
  chooseExport: (name: string) => save({ title: '导出 GeoPackage', defaultPath: `${name.replace(/[<>:"/\\|?*]/g, '_')}.gpkg`, filters: [{ name: 'GeoPackage', extensions: ['gpkg'] }] }),
  join,
  diagnosticDirectory: async (projectPath?: string) => projectPath
    ? join(await dirname(projectPath), 'cache')
    : appCacheDir(),
  onClose: async (handler: (preventDefault: () => void) => void) => getCurrentWindow().onCloseRequested((event) => handler(() => event.preventDefault())),
  closeWindow: () => getCurrentWindow().destroy(),
};

export type DesktopBridge = typeof desktop;
