import { act, cleanup, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import type { DesktopBridge } from './bridge';
import { useWorkspace } from './use-workspace';
import { validateDirectoryName } from './project-state';
import type { EngineMethod, ProbeReport, Project, RuntimeInfo, Task } from '../../../shared/contracts';

vi.mock('./components/VectorMap', () => ({ VectorMap: () => <div data-testid="vector-map" /> }));

const project: Project = {
  id: 'test-project', name: '用地检查', description: '', schemaVersion: 4,
  createdAt: '2026-09-09T00:00:00Z', updatedAt: '2026-09-09T00:00:00Z',
  projectPath: 'C:/test/用地检查/project.spa', analysisCrs: null, displayCrs: 'EPSG:3857',
  viewState: { center: [114, 27.1], zoom: 5 },
};
const runtime: RuntimeInfo = {
  protocolVersion: 4, engineVersion: '0.4.0', pythonVersion: 'test-python', packaged: false,
  versions: { GDAL: 'test-gdal' }, drivers: { GPKG: 'test-driver' }, logPath: 'C:/test/engine.log',
};
const report: ProbeReport = {
  ok: true, crs: 'EPSG:4547', expectedAreaM2: 10000, measuredAreaM2: 10000,
  expectedIntersectionAreaM2: 5000, intersectionAreaM2: 5000, areaErrorM2: 0,
  roundTripErrorM: 0, checks: [{ id: 'fixture', label: 'Synthetic fixture', passed: true, detail: 'Test only' }],
  preview: { type: 'FeatureCollection', features: [] }, sourceBounds: [500000, 3000000, 500150, 3000100],
  versions: {}, reportPath: 'C:/test/report.json', geopackagePath: 'C:/test/probe.gpkg', geotiffPath: 'C:/test/probe.tif', durationMs: 21,
};

function fixtureBridge(native = true) {
  let closeHandler: ((preventDefault: () => void) => void) | undefined;
  let tasks: Task[] = [];
  const request = vi.fn(async (method: EngineMethod, params: Record<string, unknown> = {}) => {
    switch (method) {
      case 'runtime.info': return runtime;
      case 'project.create':
      case 'project.open': return { ...project };
      case 'project.save': return { ...project, ...params, updatedAt: '2026-09-09T01:00:00Z' };
      case 'project.close': return { closed: true as const };
      case 'workspace.get': return { projectId: project.id, datasets: [], layers: [], tasks };
      case 'vector.import': {
        const task: Task = { id: 'import-1', kind: 'import', status: 'running', stage: 'inspect', completed: null, total: null, createdAt: project.createdAt, updatedAt: project.createdAt, datasetId: null, destination: null, error: null };
        tasks = [task];
        return task;
      }
      case 'task.get': return tasks.find((task) => task.id === params.taskId);
      case 'task.cancel': {
        tasks = tasks.map((task) => task.id === params.taskId ? { ...task, status: 'cancelled' as const, stage: 'cancelled' } : task);
        return tasks.find((task) => task.id === params.taskId);
      }
      case 'diagnostics.run': return report;
    }
  });
  const bridge: DesktopBridge = {
    available: () => native,
    request: request as DesktopBridge['request'],
    chooseParent: vi.fn(async () => 'C:/test'),
    chooseProject: vi.fn(async () => project.projectPath),
    chooseVector: vi.fn(async () => 'C:/test/land.gpkg'),
    chooseGdb: vi.fn(async () => 'C:/test/land.gdb'),
    selectTableSource: vi.fn(async () => 'C:/test/points.csv'),
    chooseRaster: vi.fn(async () => 'C:/test/image.tif'),
    chooseRasterExport: vi.fn(async () => 'C:/test/export.tif'),
    chooseExport: vi.fn(async () => 'C:/test/export.gpkg'),
    join: vi.fn(async (...paths: string[]) => paths.join('/')),
    diagnosticDirectory: vi.fn(async (path?: string) => path ? 'C:/test/用地检查/cache' : 'C:/test/app-cache'),
    onClose: vi.fn(async (handler) => { closeHandler = handler; return () => undefined; }),
    closeWindow: vi.fn(async () => undefined),
  };
  return { bridge, request, triggerClose: (prevent: () => void) => closeHandler?.(prevent) };
}

async function openFixture() {
  const fixture = fixtureBridge();
  const hook = renderHook(() => useWorkspace(fixture.bridge));
  await waitFor(() => expect(hook.result.current.runtime).toEqual(runtime));
  await act(async () => hook.result.current.requestAction('open'));
  await waitFor(() => expect(hook.result.current.project?.id).toBe(project.id));
  return { ...fixture, ...hook };
}

afterEach(cleanup);

describe('project transitions', () => {
  it('does not expose fake native results in a browser', () => {
    const { bridge, request } = fixtureBridge(false);
    render(<App bridge={bridge} />);
    expect(screen.getByText(/浏览器模式下本地引擎不可用/)).toBeTruthy();
    expect(screen.getByRole('button', { name: '新建项目' }).hasAttribute('disabled')).toBe(true);
    expect(screen.getByRole('button', { name: '导入数据' }).hasAttribute('disabled')).toBe(true);
    expect(request).not.toHaveBeenCalled();
    expect(screen.queryByText('10,000')).toBeNull();
  });

  it('creates only in the joined new project directory', async () => {
    const { bridge, request } = fixtureBridge();
    const { result } = renderHook(() => useWorkspace(bridge));
    await waitFor(() => expect(result.current.runtime).toEqual(runtime));
    await act(async () => { await result.current.create('用地检查', 'C:/中文 项目'); });
    expect(bridge.join).toHaveBeenCalledWith('C:/中文 项目', '用地检查');
    expect(request).toHaveBeenCalledWith('project.create', { directory: 'C:/中文 项目/用地检查', name: '用地检查' });
    expect(result.current.dirty).toBe(false);
  });

  it('protects unsaved changes before a file chooser and allows cancellation', async () => {
    const { result, bridge } = await openFixture();
    await act(async () => result.current.setDraft({ ...result.current.draft!, description: '未保存的边界说明' }));
    await act(async () => result.current.requestAction('open'));
    expect(result.current.pending).toBe('open');
    expect(bridge.chooseProject).toHaveBeenCalledTimes(1);
    await act(async () => { await result.current.resolvePending('cancel'); });
    expect(result.current.pending).toBeNull();
    expect(result.current.draft?.description).toBe('未保存的边界说明');
    expect(result.current.dirty).toBe(true);
  });

  it('keeps the draft and pending action when saving fails', async () => {
    const { result, request } = await openFixture();
    await act(async () => result.current.setDraft({ ...result.current.draft!, analysisCrs: 'invalid-crs' }));
    await act(async () => result.current.requestAction('close'));
    request.mockRejectedValueOnce({ code: -32602, message: '坐标系无效', data: { kind: 'INVALID_CRS' } });
    await act(async () => { await result.current.resolvePending('save'); });
    expect(result.current.pending).toBe('close');
    expect(result.current.dirty).toBe(true);
    expect(result.current.project?.id).toBe(project.id);
    expect(result.current.error?.message).toBe('坐标系无效');
    expect(request.mock.calls.filter(([method]) => method === 'project.close')).toHaveLength(0);
  });

  it('does not discard current state if opening another project fails', async () => {
    const { result, request } = await openFixture();
    await act(async () => result.current.setDraft({ ...result.current.draft!, description: 'retain me' }));
    await act(async () => result.current.requestAction('open'));
    request.mockRejectedValueOnce({ code: -32000, message: '项目损坏' });
    await act(async () => { await result.current.resolvePending('discard'); });
    expect(result.current.project?.id).toBe(project.id);
    expect(result.current.draft?.description).toBe('retain me');
    expect(result.current.dirty).toBe(true);
  });

  it('saves null CRS and preserves project view state before closing', async () => {
    const { result, request } = await openFixture();
    await act(async () => result.current.setDraft({ ...result.current.draft!, name: '更改名称', analysisCrs: '  ' }));
    await act(async () => result.current.requestAction('close'));
    await act(async () => { await result.current.resolvePending('save'); });
    expect(request).toHaveBeenCalledWith('project.save', {
      path: project.projectPath, name: '更改名称', description: '', analysisCrs: null,
      displayCrs: 'EPSG:3857', viewState: project.viewState,
    });
    expect(result.current.project).toBeNull();
    expect(result.current.dirty).toBe(false);
    expect(request.mock.calls.map(([method]) => method).slice(-2)).toEqual(['project.save', 'project.close']);
  });

  it('blocks native window closing until unsaved changes are resolved', async () => {
    const { result, bridge, triggerClose } = await openFixture();
    await act(async () => result.current.setDraft({ ...result.current.draft!, description: 'draft' }));
    const prevent = vi.fn();
    await act(async () => triggerClose(prevent));
    expect(prevent).toHaveBeenCalledOnce();
    expect(result.current.pending).toBe('exit');
    await act(async () => { await result.current.resolvePending('discard'); });
    expect(bridge.closeWindow).toHaveBeenCalledOnce();
  });

  it('still exits after explicit discard if engine cleanup fails', async () => {
    const { result, request, bridge, triggerClose } = await openFixture();
    await act(async () => result.current.setDraft({ ...result.current.draft!, description: 'draft' }));
    await act(async () => triggerClose(vi.fn()));
    request.mockRejectedValueOnce({ code: -32000, message: '引擎断线', data: { kind: 'ENGINE_DISCONNECTED' } });
    await act(async () => { await result.current.resolvePending('discard'); });
    expect(bridge.closeWindow).toHaveBeenCalledOnce();
  });

  it('retains a draft after engine failure without treating reconnection as project recovery', async () => {
    const { result, request } = await openFixture();
    await act(async () => result.current.setDraft({ ...result.current.draft!, description: 'recovery draft' }));
    request.mockRejectedValueOnce({ code: -32000, message: '引擎超时', data: { kind: 'ENGINE_TIMEOUT' } });
    await act(async () => { await result.current.save(); });
    expect(result.current.runtime).toBeNull();
    expect(result.current.needsReopen).toBe(true);
    await act(async () => { await result.current.connect(); });
    expect(result.current.runtime).toEqual(runtime);
    expect(result.current.needsReopen).toBe(true);
    expect(result.current.draft?.description).toBe('recovery draft');
    expect(result.current.notice).toContain('重新打开');
    const callsBeforeSave = request.mock.calls.length;
    await act(async () => { await result.current.save(); });
    expect(request.mock.calls).toHaveLength(callsBeforeSave);
  });

  it('uses the active project cache and only stores the returned diagnostic report', async () => {
    const { result, bridge, request } = await openFixture();
    expect(result.current.report).toBeNull();
    await act(async () => { await result.current.diagnose(); });
    expect(bridge.diagnosticDirectory).toHaveBeenCalledWith(project.projectPath);
    expect(request).toHaveBeenCalledWith('diagnostics.run', { directory: 'C:/test/用地检查/cache' });
    expect(result.current.report).toEqual(report);
  });

  it('refreshes workspace without replacing unsaved metadata or map view', async () => {
    const { result, request } = await openFixture();
    await act(async () => result.current.setDraft({ ...result.current.draft!, description: 'unsaved description' }));
    await act(async () => result.current.setView({ center: [113, 28], zoom: 9 }));
    await act(async () => { await result.current.refreshWorkspace(); });
    expect(request).toHaveBeenLastCalledWith('workspace.get', { path: project.projectPath });
    expect(result.current.draft?.description).toBe('unsaved description');
    expect(result.current.draft?.viewState).toEqual({ center: [113, 28], zoom: 9 });
    expect(result.current.dirty).toBe(true);
  });

  it('persists map view and resets the session when reopening the same project', async () => {
    const { result, request } = await openFixture();
    const firstSession = result.current.sessionId;
    await act(async () => result.current.setView({ center: [113, 28], zoom: 9 }));
    await act(async () => { await result.current.save(); });
    expect(request).toHaveBeenCalledWith('project.save', expect.objectContaining({ displayCrs: 'EPSG:3857', viewState: { center: [113, 28], zoom: 9 } }));
    expect(result.current.dirty).toBe(false);
    await act(async () => result.current.requestAction('open'));
    await waitFor(() => expect(result.current.sessionId).toBeGreaterThan(firstSession));
    expect(result.current.draft?.viewState).toEqual(project.viewState);
  });

  it('starts and cancels an import using the current project path and real task state', async () => {
    const { result, request } = await openFixture();
    const source = { sourcePath: 'C:/test/land.gpkg', sourceLayer: 'land', encoding: null, assignedCrs: null };
    await act(async () => { await result.current.importVector(source); });
    expect(request).toHaveBeenCalledWith('vector.import', { path: project.projectPath, ...source });
    expect(result.current.activeTask).toMatchObject({ id: 'import-1', total: null, completed: null });
    await act(async () => { await result.current.cancelTask('import-1'); });
    expect(request).toHaveBeenCalledWith('task.cancel', { path: project.projectPath, taskId: 'import-1' });
    expect(result.current.activeTask).toBeNull();
    expect(result.current.workspace?.tasks[0].status).toBe('cancelled');
  });

  it('blocks import, layer updates and refresh until the failed project is reopened', async () => {
    const { result, request } = await openFixture();
    await act(async () => result.current.handleFailure({ code: -32000, message: 'engine stopped', data: { kind: 'ENGINE_DISCONNECTED' } }));
    await act(async () => { await result.current.connect(); });
    const count = request.mock.calls.length;
    await act(async () => { await result.current.importVector({ sourcePath: 'C:/land.gpkg', sourceLayer: 'land', encoding: null, assignedCrs: null }); });
    await act(async () => { await result.current.updateLayer('layer-1', { visible: false }); });
    await act(async () => { await result.current.refreshWorkspace(); });
    expect(request.mock.calls).toHaveLength(count);
    expect(result.current.needsReopen).toBe(true);
  });
});

describe('new project folder names', () => {
  it.each(['../escape', 'a\\b', 'CON', 'aux.txt', 'lpt1', 'name.', ' name', ''])('rejects unsafe Windows name %s', (name) => {
    expect(validateDirectoryName(name)).not.toBeNull();
  });
  it('accepts a Chinese name with spaces', () => {
    expect(validateDirectoryName('张家界 用地')).toBeNull();
  });
});
