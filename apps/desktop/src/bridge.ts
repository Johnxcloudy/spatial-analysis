import { invoke, isTauri } from '@tauri-apps/api/core';
import { appCacheDir, dirname, join } from '@tauri-apps/api/path';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { open, save } from '@tauri-apps/plugin-dialog';
import type { AttributePage, EngineError, EngineMethod, FeatureResult, MapLayer, ProbeReport, Project, RuntimeInfo, SourceInspection, Task, ViewportResult, Workspace } from '../../../shared/contracts';

interface EngineResults {
  'runtime.info': RuntimeInfo;
  'project.create': Project;
  'project.open': Project;
  'project.save': Project;
  'project.close': { closed: true };
  'diagnostics.run': ProbeReport;
  'source.inspect': SourceInspection;
  'workspace.get': Workspace;
  'vector.import': Task;
  'vector.export': Task;
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
): Promise<EngineResults[M]> {
  if (!isTauri()) {
    throw { code: -32000, message: '浏览器模式下本地引擎不可用，请使用桌面应用。', data: { kind: 'NATIVE_UNAVAILABLE' } } satisfies EngineError;
  }
  try {
    return await invoke<EngineResults[M]>('engine_request', { method, params });
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
  chooseExport: (name: string) => save({ title: '导出 GeoPackage', defaultPath: `${name.replace(/[<>:"/\\|?*]/g, '_')}.gpkg`, filters: [{ name: 'GeoPackage', extensions: ['gpkg'] }] }),
  join,
  diagnosticDirectory: async (projectPath?: string) => projectPath
    ? join(await dirname(projectPath), 'cache')
    : appCacheDir(),
  onClose: async (handler: (preventDefault: () => void) => void) => getCurrentWindow().onCloseRequested((event) => handler(() => event.preventDefault())),
  closeWindow: () => getCurrentWindow().destroy(),
};

export type DesktopBridge = typeof desktop;
