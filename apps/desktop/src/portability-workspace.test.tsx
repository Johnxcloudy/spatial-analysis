import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { open } from '@tauri-apps/plugin-dialog';
import type { Dataset, EngineMethod, Project, SourceStatus, Task, Workspace } from '../../../shared/contracts';
import { desktop, type DesktopBridge } from './bridge';
import { useWorkspace, type WorkspaceState } from './use-workspace';
import App from './App';
import { SourceLocation } from './components/SourceLocation';

vi.mock('./components/VectorMap', () => ({ VectorMap: () => <div data-testid="vector-map" /> }));
vi.mock('@tauri-apps/plugin-dialog', () => ({ open: vi.fn(), save: vi.fn() }));
const dialogMethods = ['showModal', 'close'] as const;
const descriptors = dialogMethods.map((method) => Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, method));
beforeAll(() => Object.defineProperties(HTMLDialogElement.prototype, { showModal: { configurable: true, value() { this.setAttribute('open', ''); } }, close: { configurable: true, value() { this.removeAttribute('open'); } } }));
afterAll(() => dialogMethods.forEach((method, index) => { const descriptor = descriptors[index]; if (descriptor) Object.defineProperty(HTMLDialogElement.prototype, method, descriptor); else Reflect.deleteProperty(HTMLDialogElement.prototype, method); }));

const project: Project = { id: 'original', name: '规划', description: '', schemaVersion: 7, createdAt: '2026-09-10T00:00:00Z', updatedAt: '2026-09-10T00:00:00Z', projectPath: 'C:/projects/original/project.spa', analysisCrs: null, displayCrs: 'EPSG:3857', viewState: { center: [114, 27], zoom: 5 } };
const copy: Project = { ...project, id: 'copy', name: '草稿名称', description: '未保存说明', analysisCrs: 'EPSG:4547', projectPath: 'C:/copies/new-copy/project.spa', viewState: { center: [113, 28], zoom: 9 } };
const baseTask: Task = { id: 'copy-task', kind: 'save_as', status: 'running', stage: 'copying', completed: 0, total: 10, createdAt: project.createdAt, updatedAt: project.updatedAt, datasetId: null, destination: copy.projectPath, error: null };
const dataset: Dataset = { kind: 'table', id: 'table', name: '坐标表', version: 'v1', source: { path: 'C:/old/points.csv', layer: 'records', driver: 'CSV', fingerprint: 'fingerprint', encoding: 'utf-8', assignedCrs: null, crsWkt: null, metadata: {} }, relativePath: 'tables/table.gpkg', storageLayer: 'records', cellMetadataLayer: null, featureCount: 1, geometryType: null, crsWkt: null, crsAuthority: null, bounds: null, boundsWgs84: null, fields: [], internalIdField: '_id', sourceFidField: '_source', report: { status: 'warning', checks: [], warnings: [], notChecked: [], counts: {}, validatorVersion: 'test' }, createdAt: project.createdAt };

function fixture(initialTasks: Task[] = []) {
  let current = project;
  let tasks = initialTasks;
  let finalTask: Task = baseTask;
  let status: SourceStatus = { datasetId: dataset.id, originalPath: dataset.source.path, resolvedPath: dataset.source.path, availability: 'missing', relocated: false, verifiedAt: null };
  let preventClose: ((prevent: () => void) => void) | undefined;
  const request = vi.fn(async (method: EngineMethod, params: Record<string, unknown> = {}) => {
    switch (method) {
      case 'runtime.info': return { protocolVersion: 7, engineVersion: '0.5.0', versions: {}, drivers: {} };
      case 'project.open': current = params.path === copy.projectPath ? copy : project; return current;
      case 'workspace.get': return { projectId: current.id, datasets: [dataset], layers: [], tasks } satisfies Workspace;
      case 'project.saveAs': tasks = [baseTask, ...tasks]; return baseTask;
      case 'task.get': tasks = tasks.map((item) => item.id === finalTask.id ? finalTask : item); return finalTask;
      case 'task.cancel': tasks = tasks.map((item) => item.id === params.taskId ? { ...item, status: 'cancelled', stage: 'cancelled' } : item); return tasks.find((item) => item.id === params.taskId);
      case 'project.close': return { closed: true };
      case 'source.status': return status;
      case 'source.relocate': {
        const task: Task = { ...baseTask, id: 'relocate-task', kind: 'relocate', datasetId: dataset.id, destination: String(params.sourcePath) };
        tasks = [task, ...tasks]; finalTask = task; return task;
      }
      case 'table.page': return { datasetId: dataset.id, version: dataset.version, fields: [], rows: [], total: 0, offset: 0, limit: 100, hasMore: false, truncated: false };
      default: throw new Error(`Unexpected method ${method}`);
    }
  });
  const bridge: DesktopBridge = { ...desktop, available: () => true, request: request as DesktopBridge['request'], chooseProject: vi.fn(async () => project.projectPath), chooseParent: vi.fn(async () => 'C:/copies'), chooseSource: vi.fn(async () => 'C:/moved/points.csv'), join: vi.fn(async (...parts: string[]) => parts.join('/')), onClose: vi.fn(async (handler) => { preventClose = handler; return () => undefined; }) };
  return { bridge, request, setFinalTask: (value: Task) => { finalTask = value; }, setStatus: (value: SourceStatus) => { status = value; }, triggerClose: (prevent: () => void) => preventClose?.(prevent) };
}

async function openFixture(initialTasks: Task[] = []) {
  const f = fixture(initialTasks);
  const hook = renderHook(() => useWorkspace(f.bridge));
  await waitFor(() => expect(hook.result.current.runtime).not.toBeNull());
  await act(async () => hook.result.current.requestAction('open'));
  await waitFor(() => expect(hook.result.current.workspace).not.toBeNull());
  return { ...f, ...hook };
}

async function editDraft(result: Awaited<ReturnType<typeof openFixture>>['result']) {
  await act(async () => result.current.setDraft({ ...result.current.draft!, name: copy.name, description: copy.description, analysisCrs: copy.analysisCrs!, viewState: copy.viewState }));
}

afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('project portability', () => {
  it('copies the current draft without saving the original and opens only its successful task', async () => {
    const f = await openFixture();
    await editDraft(f.result);
    await act(async () => { await f.result.current.saveAs('new-copy', 'C:/copies'); });
    expect(f.request).toHaveBeenCalledWith('project.saveAs', { path: project.projectPath, directory: 'C:/copies/new-copy', name: copy.name, description: copy.description, analysisCrs: copy.analysisCrs, displayCrs: project.displayCrs, viewState: copy.viewState });
    expect(f.result.current.project?.id).toBe(project.id);
    expect(f.result.current.copying).toBe(true);
    expect(f.request.mock.calls.some(([method]) => method === 'project.save')).toBe(false);
    f.setFinalTask({ ...baseTask, status: 'completed', stage: 'completed' });
    await waitFor(() => expect(f.result.current.project?.id).toBe(copy.id));
    expect(f.result.current.draft?.description).toBe(copy.description);
    expect(f.result.current.dirty).toBe(false);
    expect(f.result.current.copying).toBe(false);
  });

  it.each(['failed', 'cancelled', 'interrupted'] as const)('preserves the original draft after a %s task', async (status) => {
    const f = await openFixture();
    await editDraft(f.result);
    await act(async () => { await f.result.current.saveAs('new-copy', 'C:/copies'); });
    f.setFinalTask({ ...baseTask, status, stage: status, error: status === 'failed' ? '磁盘空间不足' : null });
    await waitFor(() => expect(f.result.current.copying).toBe(false));
    expect(f.result.current.project?.id).toBe(project.id);
    expect(f.result.current.draft?.description).toBe(copy.description);
    expect(f.result.current.dirty).toBe(true);
    expect(f.request.mock.calls.filter(([method]) => method === 'project.open')).toHaveLength(1);
  });

  it('keeps the draft after a rejected destination and never auto-opens historical successful copies', async () => {
    const f = await openFixture([{ ...baseTask, status: 'completed' }]);
    await editDraft(f.result);
    f.request.mockRejectedValueOnce(new Error('目标目录已经存在'));
    await act(async () => { expect(await f.result.current.saveAs('new-copy', 'C:/copies')).toBe(false); });
    expect(f.result.current.error?.message).toBe('目标目录已经存在');
    expect(f.result.current.copying).toBe(false);
    expect(f.result.current.project?.id).toBe(project.id);
    expect(f.result.current.draft?.description).toBe(copy.description);
    expect(f.request.mock.calls.filter(([method]) => method === 'project.open')).toHaveLength(1);
  });

  it('blocks draft mutations, saves, switches and native close while copying, but allows cancellation', async () => {
    const f = await openFixture();
    await editDraft(f.result);
    await act(async () => { await f.result.current.saveAs('new-copy', 'C:/copies'); });
    const count = f.request.mock.calls.length;
    const prevent = vi.fn();
    await act(async () => {
      f.result.current.setDraft({ ...f.result.current.draft!, description: 'late edit' });
      f.result.current.setView({ center: [0, 0], zoom: 3 });
      f.result.current.requestAction('open');
      f.triggerClose(prevent);
      await f.result.current.save();
      await f.result.current.updateLayer('layer', { visible: false });
    });
    expect(f.request.mock.calls).toHaveLength(count);
    expect(prevent).toHaveBeenCalledOnce();
    expect(f.result.current.pending).toBeNull();
    expect(f.result.current.draft?.description).toBe(copy.description);
    expect(f.result.current.draft?.viewState).toEqual(copy.viewState);
    await act(async () => { await f.result.current.cancelTask(baseTask.id); });
    await waitFor(() => expect(f.result.current.copying).toBe(false));
    expect(f.result.current.project?.id).toBe(project.id);
    expect(f.result.current.dirty).toBe(true);
  });

  it('drops an old task completion after the engine session fails', async () => {
    const f = await openFixture();
    await editDraft(f.result);
    let finish!: (value: Task) => void;
    await act(async () => { await f.result.current.saveAs('new-copy', 'C:/copies'); });
    f.request.mockImplementationOnce(async () => new Promise<Task>((resolve) => { finish = resolve; }));
    await waitFor(() => expect(finish).toBeTypeOf('function'));
    await act(async () => f.result.current.handleFailure({ message: '断开连接', data: { kind: 'ENGINE_DISCONNECTED' } }));
    await act(async () => finish({ ...baseTask, status: 'completed' }));
    expect(f.result.current.project?.id).toBe(project.id);
    expect(f.result.current.draft?.description).toBe(copy.description);
    expect(f.result.current.needsReopen).toBe(true);
    expect(f.request.mock.calls.filter(([method]) => method === 'project.open')).toHaveLength(1);
  });

  it('ignores a delayed poll completion after cancellation has already finished', async () => {
    const f = await openFixture();
    await editDraft(f.result);
    let finish!: (value: Task) => void;
    await act(async () => { await f.result.current.saveAs('new-copy', 'C:/copies'); });
    f.request.mockImplementationOnce(async () => new Promise<Task>((resolve) => { finish = resolve; }));
    await waitFor(() => expect(finish).toBeTypeOf('function'));
    await act(async () => { await f.result.current.cancelTask(baseTask.id); });
    await act(async () => finish({ ...baseTask, status: 'completed' }));
    expect(f.result.current.workspace?.tasks[0].status).toBe('cancelled');
    expect(f.result.current.project?.id).toBe(project.id);
    expect(f.result.current.draft?.description).toBe(copy.description);
    expect(f.request.mock.calls.filter(([method]) => method === 'project.open')).toHaveLength(1);
  });

  it('opens the successful copy once when cancellation arrives after publication', async () => {
    const f = await openFixture();
    const original = f.request.getMockImplementation()!;
    f.request.mockImplementation(async (method, params) => original(method === 'task.cancel' ? 'task.get' : method, params));
    await act(async () => { await f.result.current.saveAs('new-copy', 'C:/copies'); });
    f.setFinalTask({ ...baseTask, status: 'completed' });
    await act(async () => { await f.result.current.cancelTask(baseTask.id); });
    await waitFor(() => expect(f.result.current.project?.id).toBe(copy.id));
    expect(f.request.mock.calls.filter(([method, params]) => method === 'project.open' && params?.path === copy.projectPath)).toHaveLength(1);
  });

  it('retains the original draft if opening a published copy fails', async () => {
    const f = await openFixture();
    await editDraft(f.result);
    const original = f.request.getMockImplementation()!;
    f.request.mockImplementation(async (method, params) => {
      if (method === 'project.open' && params?.path === copy.projectPath) throw new Error('副本打开失败');
      return original(method, params);
    });
    await act(async () => { await f.result.current.saveAs('new-copy', 'C:/copies'); });
    f.setFinalTask({ ...baseTask, status: 'completed' });
    await waitFor(() => expect(f.result.current.error?.message).toContain('副本打开失败'));
    expect(f.result.current.project?.id).toBe(project.id);
    expect(f.result.current.draft?.description).toBe(copy.description);
    expect(f.result.current.copying).toBe(false);
  });

  it('offers a new child directory in Save As and reports missing source separately from immutable provenance', async () => {
    const f = fixture();
    render(<App bridge={f.bridge} />);
    await waitFor(() => expect(screen.getByRole('button', { name: '打开项目' }).hasAttribute('disabled')).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: '打开项目' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '项目另存为' }).hasAttribute('disabled')).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: '项目另存为' }));
    expect(screen.getByRole('dialog', { name: '项目另存为' })).toBeTruthy();
    fireEvent.change(screen.getByLabelText('新目录名称'), { target: { value: 'new-copy' } });
    fireEvent.click(screen.getByRole('button', { name: '选择父目录' }));
    await waitFor(() => expect((screen.getByLabelText('父目录') as HTMLInputElement).value).toBe('C:/copies'));
    fireEvent.click(screen.getAllByRole('button', { name: '取消' })[0]);
    expect(f.request.mock.calls.some(([method]) => method === 'project.saveAs')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '数据与检查报告' }));
    await waitFor(() => expect(screen.getByText('来源缺失')).toBeTruthy());
    expect(screen.getAllByText(dataset.source.path).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: '重新定位来源' }));
    await waitFor(() => expect(f.request).toHaveBeenCalledWith('source.relocate', { path: project.projectPath, datasetId: dataset.id, sourcePath: 'C:/moved/points.csv' }));
    expect(f.bridge.chooseSource).toHaveBeenCalledWith(dataset);
  });

  it('preserves the draft and requires recovery when the published copy workspace cannot load', async () => {
    const f = await openFixture();
    await editDraft(f.result);
    const original = f.request.getMockImplementation()!;
    f.request.mockImplementation(async (method, params) => {
      if (method === 'workspace.get' && params?.path === copy.projectPath) throw new Error('副本工作区加载失败');
      return original(method, params);
    });
    await act(async () => { await f.result.current.saveAs('new-copy', 'C:/copies'); });
    f.setFinalTask({ ...baseTask, status: 'completed' });
    await waitFor(() => expect(f.result.current.error?.message).toContain('副本工作区加载失败'));
    expect(f.result.current.project?.id).toBe(project.id);
    expect(f.result.current.draft?.description).toBe(copy.description);
    expect(f.result.current.dirty).toBe(true);
    expect(f.result.current.needsReopen).toBe(true);
  });

  it('does not reuse a cancelled Save As error for a new target or reopened dialog', async () => {
    const f = fixture();
    const original = f.request.getMockImplementation()!;
    f.request.mockImplementation(async (method, params) => {
      if (method === 'project.saveAs') throw new Error('目标目录已经存在');
      return original(method, params);
    });
    render(<App bridge={f.bridge} />);
    await waitFor(() => expect(screen.getByRole('button', { name: '打开项目' }).hasAttribute('disabled')).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: '打开项目' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '项目另存为' }).hasAttribute('disabled')).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: '项目另存为' }));
    fireEvent.change(screen.getByLabelText('新目录名称'), { target: { value: 'existing' } });
    fireEvent.click(screen.getByRole('button', { name: '选择父目录' }));
    await waitFor(() => expect((screen.getByLabelText('父目录') as HTMLInputElement).value).toBe('C:/copies'));
    fireEvent.click(screen.getByRole('button', { name: '另存项目' }));
    const dialog = screen.getByRole('dialog', { name: '项目另存为' });
    await waitFor(() => expect(within(dialog).getByRole('alert').textContent).toContain('目标目录已经存在'));
    fireEvent.change(screen.getByLabelText('新目录名称'), { target: { value: 'new-target' } });
    expect(within(dialog).queryByRole('alert')).toBeNull();
    fireEvent.click(screen.getAllByRole('button', { name: '取消' })[0]);
    fireEvent.click(screen.getByRole('button', { name: '项目另存为' }));
    expect(within(screen.getByRole('dialog', { name: '项目另存为' })).queryByRole('alert')).toBeNull();
  });

  it('preserves the old draft and requires recovery if a normal open cannot load its workspace', async () => {
    const f = await openFixture();
    await editDraft(f.result);
    const original = f.request.getMockImplementation()!;
    f.request.mockImplementation(async (method, params) => {
      if (method === 'workspace.get' && params?.path === copy.projectPath) throw new Error('项目工作区加载失败');
      return original(method, params);
    });
    vi.mocked(f.bridge.chooseProject).mockResolvedValueOnce(copy.projectPath);
    await act(async () => { f.result.current.requestAction('open'); });
    await act(async () => { await f.result.current.resolvePending('discard'); });
    expect(f.result.current.error?.message).toBe('项目工作区加载失败');
    expect(f.result.current.project?.id).toBe(project.id);
    expect(f.result.current.draft?.description).toBe(copy.description);
    expect(f.result.current.needsReopen).toBe(true);
    const count = f.request.mock.calls.length;
    await act(async () => { await f.result.current.save(); });
    expect(f.request.mock.calls.length).toBe(count);
  });

  it('keeps source provenance and the current draft when a relocation identity mismatches', async () => {
    const f = await openFixture();
    await editDraft(f.result);
    await act(async () => { await f.result.current.relocateSource(dataset); });
    f.setFinalTask({ ...baseTask, id: 'relocate-task', kind: 'relocate', datasetId: dataset.id, destination: 'C:/moved/points.csv', status: 'failed', stage: 'failed', error: '来源内容不匹配' });
    await waitFor(() => expect(f.result.current.notice).toBe('来源内容不匹配'));
    expect(f.result.current.workspace?.datasets[0].source).toEqual(dataset.source);
    expect(f.result.current.draft?.description).toBe(copy.description);
    expect(f.result.current.dirty).toBe(true);
  });
});

describe('source location', () => {
  const present: SourceStatus = { datasetId: dataset.id, originalPath: dataset.source.path, resolvedPath: 'C:/moved/points.csv', availability: 'present', relocated: true, verifiedAt: '2026-09-10T00:00:00Z' };
  function sourceState(): WorkspaceState { return { native: true, runtime: {}, needsReopen: false, project, sessionId: 1, workspace: { tasks: [] }, handleFailure: vi.fn(), busy: null, activeTask: null, copying: false, relocateSource: vi.fn() } as unknown as WorkspaceState; }

  it('distinguishes current presence from a historical identity check', async () => {
    const f = fixture(); f.setStatus(present);
    render(<SourceLocation bridge={f.bridge} state={sourceState()} dataset={dataset} />);
    await waitFor(() => expect(screen.getByText('来源存在（当前内容未核对）')).toBeTruthy());
    expect(screen.getByText('上次身份核对（历史记录）')).toBeTruthy();
    expect(screen.getByText(present.resolvedPath)).toBeTruthy();
  });

  it('ignores late source status results for a previous dataset', async () => {
    const f = fixture();
    let finish!: (status: SourceStatus) => void;
    f.request.mockImplementationOnce(async () => new Promise<SourceStatus>((resolve) => { finish = resolve; }));
    const state = sourceState();
    const view = render(<SourceLocation bridge={f.bridge} state={state} dataset={dataset} />);
    const other = { ...dataset, id: 'other', source: { ...dataset.source, path: 'C:/other.csv' } };
    f.setStatus({ ...present, datasetId: other.id, originalPath: other.source.path, resolvedPath: other.source.path, availability: 'missing' });
    view.rerender(<SourceLocation bridge={f.bridge} state={state} dataset={other} />);
    await waitFor(() => expect(screen.getByText('来源缺失')).toBeTruthy());
    await act(async () => finish(present));
    expect(screen.queryByText(present.resolvedPath)).toBeNull();
    expect(screen.getByText(other.source.path)).toBeTruthy();
  });

  it('shows derived sources as internal and disables external relocation', async () => {
    const f = fixture();
    const derived = { ...dataset, source: { ...dataset.source, driver: 'TablePoints' } };
    f.setStatus({ ...present, availability: 'internal', resolvedPath: 'C:/projects/original/tables/parent.gpkg', relocated: false, verifiedAt: null });
    render(<SourceLocation bridge={f.bridge} state={sourceState()} dataset={derived} />);
    await waitFor(() => expect(screen.getByText('项目内部来源')).toBeTruthy());
    expect(screen.getByRole('button', { name: '重新定位来源' }).hasAttribute('disabled')).toBe(true);
  });

  it.each([
    ['table', 'CSV', false, ['csv']], ['table', 'XLSX', false, ['xlsx']],
    ['raster', 'GTiff', false, ['tif', 'tiff']], ['vector', 'GPKG', false, ['gpkg']],
    ['vector', 'ESRI Shapefile', false, ['shp']], ['vector', 'GeoJSON', false, ['geojson', 'json']],
    ['vector', 'OpenFileGDB', true, null],
  ] as const)('selects %s %s candidates using their actual container', async (kind, driver, directory, extensions) => {
    vi.mocked(open).mockClear().mockResolvedValueOnce(null);
    await desktop.chooseSource({ ...dataset, kind, source: { ...dataset.source, driver } } as Dataset);
    expect(open).toHaveBeenCalledWith(expect.objectContaining({ directory, multiple: false }));
    if (extensions) expect(open).toHaveBeenCalledWith(expect.objectContaining({ filters: [{ name: driver, extensions }] }));
  });
});
