import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { AttributePage, EngineMethod, Project, TableDataset, TableInspection, TableOptions, Task, VectorDataset, Workspace } from '../../../shared/contracts';
import type { DesktopBridge } from './bridge';
import { AttributeTable } from './components/AttributeTable';
import { GeneratePoints } from './components/GeneratePoints';
import { ImportTable } from './components/ImportTable';
import { VectorWorkspace } from './components/VectorWorkspace';
import { initialAttributeQuery } from './use-vector-data';
import { useWorkspace, type WorkspaceState } from './use-workspace';

vi.mock('./components/VectorMap', () => ({ VectorMap: () => <div data-testid="vector-map" /> }));

const dialogMethods = ['showModal', 'close'] as const;
const dialogDescriptors = dialogMethods.map((method) => Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, method));
beforeAll(() => {
  // JSDOM lacks the native dialog methods used by the desktop WebView.
  Object.defineProperties(HTMLDialogElement.prototype, {
    showModal: { configurable: true, value() { this.setAttribute('open', ''); } },
    close: { configurable: true, value() { this.removeAttribute('open'); } },
  });
});
afterAll(() => dialogMethods.forEach((method, index) => {
  const descriptor = dialogDescriptors[index];
  if (descriptor) Object.defineProperty(HTMLDialogElement.prototype, method, descriptor);
  else Reflect.deleteProperty(HTMLDialogElement.prototype, method);
}));

const project: Project = { id: 'tables-project', name: 'Tables', description: '', schemaVersion: 4, createdAt: '2026-09-09T00:00:00Z', updatedAt: '2026-09-09T00:00:00Z', projectPath: 'C:/test/project.spa', analysisCrs: null, displayCrs: 'EPSG:3857', viewState: { center: [114, 27], zoom: 5 } };
const runtime = { protocolVersion: 4, engineVersion: '0.4.0', pythonVersion: 'test', packaged: false, versions: {}, drivers: {}, logPath: 'test' };
const table: TableDataset = {
  id: 'table-1', version: 'table-version', name: 'Coordinates', kind: 'table',
  source: { path: 'C:/test/points.csv', layer: 'records', driver: 'CSV', fingerprint: 'fixture', encoding: 'utf-8-sig', assignedCrs: null, crsWkt: null, metadata: { delimiter: ',' } },
  relativePath: 'data/table.gpkg', storageLayer: 'records', cellMetadataLayer: null,
  featureCount: 2, geometryType: null, crsWkt: null, crsAuthority: null, bounds: null, boundsWgs84: null,
  fields: ['code', 'x', 'y'].map((name) => ({ name, sourceType: 'String', storageType: 'String', nullable: true, alias: null, width: null, precision: null, metadataStatus: 'partial' })),
  internalIdField: '_id', sourceFidField: '_source_row', report: { status: 'warning', checks: [], warnings: [], notChecked: [], counts: {}, validatorVersion: 'fixture' }, createdAt: project.createdAt,
};
const points: VectorDataset = { ...table, id: 'point-1', version: 'point-version', name: 'Derived points', kind: 'vector', storageLayer: 'features', geometryType: 'Point', crsWkt: 'fixture-crs', crsAuthority: 'EPSG:4326', bounds: [113, 27, 115, 29], boundsWgs84: [113, 27, 115, 29] };
const tableOptions: TableOptions = { sourcePath: 'C:/test/points.csv', encoding: 'utf-8-sig', delimiter: ',', sheet: null, headerRow: 1 };
const page: AttributePage = { datasetId: table.id, version: table.version, fields: table.fields, rows: [{ id: '1', sourceRow: '2', values: { code: '001', x: '114', y: '27' } }, { id: '2', sourceRow: '3', values: { code: '', x: 'NULL', y: null } }], total: 2, offset: 0, limit: 200, hasMore: false, truncated: false };
const inspection: TableInspection = { sourcePath: tableOptions.sourcePath, driver: 'CSV', sheets: [], sheet: null, columns: table.fields.map((field, index) => ({ index, sourceName: field.name, fieldName: field.name })), rows: [{ sourceRow: 2, values: { code: '001', x: '114', y: '27' } }], truncated: true, warnings: [] };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((accept, fail) => { resolve = accept; reject = fail; });
  return { promise, resolve, reject };
}

function fixtureBridge() {
  let workspace: Workspace = { projectId: project.id, datasets: [table], layers: [], tasks: [] };
  let completeTask = false;
  const request = vi.fn(async (method: EngineMethod, params: Record<string, unknown> = {}) => {
    switch (method) {
      case 'runtime.info': return runtime;
      case 'project.open': return project;
      case 'workspace.get': return workspace;
      case 'table.page': return (params.filter as { value: string } | null)?.value === 'missing' ? { ...page, rows: [], total: 0 } : page;
      case 'table.inspect': return inspection;
      case 'table.import':
      case 'table.export':
      case 'table.points': {
        const task: Task = { id: `task-${workspace.tasks.length}`, kind: method === 'table.points' ? 'points' : method === 'table.export' ? 'export' : 'import', status: 'running', stage: 'queued', completed: null, total: null, createdAt: project.createdAt, updatedAt: project.createdAt, datasetId: null, destination: null, error: null };
        workspace = { ...workspace, tasks: [task, ...workspace.tasks] };
        return task;
      }
      case 'task.get': {
        const task = workspace.tasks.find((item) => item.id === params.taskId)!;
        if (!completeTask) return task;
        const completed: Task = { ...task, status: 'completed', stage: 'completed', datasetId: task.kind === 'points' ? points.id : table.id };
        workspace = { ...workspace, tasks: workspace.tasks.map((item) => item.id === completed.id ? completed : item) };
        if (task.kind === 'points') workspace = { ...workspace, datasets: [table, points], layers: [{ id: 'point-layer', datasetId: points.id, name: points.name, visible: true, opacity: 1, color: '#326b54', categoryField: null, categoryColors: {}, order: 0 }] };
        return completed;
      }
    }
    throw new Error(`Unexpected fixture method: ${method}`);
  });
  const bridge: DesktopBridge = {
    available: () => true, request: request as DesktopBridge['request'], chooseParent: vi.fn(async () => 'C:/test'), chooseProject: vi.fn(async () => project.projectPath),
    chooseVector: vi.fn(async () => 'C:/test/land.gpkg'), chooseGdb: vi.fn(async () => 'C:/test/land.gdb'), chooseExport: vi.fn(async () => 'C:/test/export.gpkg'), selectTableSource: vi.fn(async () => tableOptions.sourcePath),
    chooseRaster: vi.fn(async () => 'C:/test/image.tif'), chooseRasterExport: vi.fn(async () => 'C:/test/export.tif'),
    join: vi.fn(async (...parts) => parts.join('/')), diagnosticDirectory: vi.fn(async () => 'C:/test/cache'), onClose: vi.fn(async () => () => undefined), closeWindow: vi.fn(async () => undefined),
  };
  return { bridge, request, finishTask: () => { completeTask = true; } };
}

async function openHook() {
  const fixture = fixtureBridge();
  const hook = renderHook(() => useWorkspace(fixture.bridge));
  await waitFor(() => expect(hook.result.current.runtime).toEqual(runtime));
  await act(async () => hook.result.current.requestAction('open'));
  await waitFor(() => expect(hook.result.current.workspace?.projectId).toBe(project.id));
  return { ...fixture, ...hook };
}

function modalState(overrides: Partial<WorkspaceState> = {}) {
  return { busy: null, needsReopen: false, error: null, activeTask: null, handleFailure: vi.fn(), inspectTable: vi.fn(async () => inspection), importTable: vi.fn(async () => true), generatePoints: vi.fn(async () => true), ...overrides } as unknown as WorkspaceState;
}

afterEach(cleanup);

describe('table project operations', () => {
  it('uses explicit parser options and the current project path for import and export', async () => {
    const { result, request } = await openHook();
    await act(async () => { await result.current.importTable(tableOptions); });
    expect(request).toHaveBeenCalledWith('table.import', { path: project.projectPath, ...tableOptions });
    await act(async () => { await result.current.exportTable(table.id, table.name); });
    expect(request).toHaveBeenCalledWith('table.export', { path: project.projectPath, datasetId: table.id, destination: 'C:/test/export.gpkg' });
  });

  it('keeps table selection, unsaved project metadata and map view when refreshing', async () => {
    const { result } = await openHook();
    await act(async () => result.current.setDraft({ ...result.current.draft!, description: 'draft', viewState: { center: [115, 28], zoom: 9 } }));
    await act(async () => result.current.setSelectedTableId(table.id));
    await act(async () => { await result.current.refreshWorkspace(); });
    expect(result.current.selectedTableId).toBe(table.id);
    expect(result.current.selectedLayerId).toBeNull();
    expect(result.current.draft).toMatchObject({ description: 'draft', viewState: { center: [115, 28], zoom: 9 } });
    expect(result.current.dirty).toBe(true);
  });

  it('selects a completed derived vector while retaining its source table and draft', async () => {
    const { result, request, finishTask } = await openHook();
    await act(async () => result.current.setDraft({ ...result.current.draft!, description: 'retained draft' }));
    await act(async () => { await result.current.generatePoints(table.id, 'x', 'y', 'EPSG:4326'); });
    expect(request).toHaveBeenCalledWith('table.points', { path: project.projectPath, datasetId: table.id, xField: 'x', yField: 'y', declaredCrs: 'EPSG:4326' });
    finishTask();
    await waitFor(() => expect(result.current.selectedLayerId).toBe('point-layer'));
    expect(result.current.selectedTableId).toBeNull();
    expect(result.current.workspace?.datasets.map((item) => item.id)).toEqual([table.id, points.id]);
    expect(result.current.draft?.description).toBe('retained draft');
    expect(result.current.notice).toBe('点数据已生成');
  });

  it('blocks table inspection, import, export and point conversion after engine failure', async () => {
    const { result, request } = await openHook();
    await act(async () => result.current.handleFailure({ code: -32000, message: 'stopped', data: { kind: 'ENGINE_DISCONNECTED' } }));
    const count = request.mock.calls.length;
    expect(() => result.current.inspectTable(tableOptions)).toThrow('重新打开');
    await act(async () => { await result.current.importTable(tableOptions); await result.current.exportTable(table.id, table.name); await result.current.generatePoints(table.id, 'x', 'y', 'EPSG:4326'); });
    expect(request.mock.calls).toHaveLength(count);
  });
});

describe('table attribute workspace', () => {
  it('shows the source row for a derived feature selected outside the current page', () => {
    const derived = { ...points, source: { ...points.source, driver: 'TablePoints' } };
    render(<AttributeTable dataset={derived} page={{ ...page, datasetId: derived.id, version: derived.version, rows: [] }} loading={false} query={initialAttributeQuery} setQuery={vi.fn()} selectedId="17" selected={{ datasetId: derived.id, version: derived.version, row: { id: '17', sourceRow: '29', values: { code: 'invalid point' } }, feature: null, boundsWgs84: null }} enabled onSelect={vi.fn()} onClear={vi.fn()} />);
    expect(screen.getByRole('columnheader', { name: '源行号' })).toBeTruthy();
    expect(screen.getByText('源行号：29')).toBeTruthy();
    expect(screen.getByText('不在当前页')).toBeTruthy();
  });

  it('uses table paging and row selection without map or geometry queries', async () => {
    const { bridge, request } = fixtureBridge();
    const onFit = vi.fn();
    function Harness() {
      const state = useWorkspace(bridge);
      return <><button disabled={!state.runtime} onClick={() => state.requestAction('open')}>Open fixture</button><VectorWorkspace bridge={bridge} state={state} fitRequest={null} onFit={onFit} /></>;
    }
    render(<Harness />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Open fixture' }).hasAttribute('disabled')).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: 'Open fixture' }));
    fireEvent.click(await screen.findByTitle('选择记录 1'));
    expect(await screen.findByText('选中 ID：1')).toBeTruthy();
    expect(screen.getByRole('columnheader', { name: '源行号' })).toBeTruthy();
    expect(screen.getByText('源行号：2')).toBeTruthy();
    expect(screen.queryByTestId('vector-map')).toBeNull();
    expect(onFit).not.toHaveBeenCalled();
    expect(request.mock.calls.some(([method]) => method.startsWith('vector.'))).toBe(false);
    expect(screen.getByTitle('001')).toBeTruthy();
    expect(screen.getByTitle('""')).toBeTruthy();
    const nullCells = screen.getAllByTitle('NULL');
    expect(nullCells.map((cell) => cell.classList.contains('null-value'))).toEqual([false, true]);
    fireEvent.change(screen.getByLabelText('筛选字段'), { target: { value: 'code' } });
    fireEvent.change(screen.getByLabelText('筛选值'), { target: { value: '001' } });
    fireEvent.click(screen.getByRole('button', { name: '应用筛选' }));
    await waitFor(() => expect(request).toHaveBeenCalledWith('table.page', { path: project.projectPath, datasetId: table.id, ...initialAttributeQuery, filter: { field: 'code', operator: 'contains', value: '001' } }));
    fireEvent.change(screen.getByLabelText('筛选值'), { target: { value: 'missing' } });
    fireEvent.click(screen.getByRole('button', { name: '应用筛选' }));
    expect(await screen.findByText('没有符合条件的记录')).toBeTruthy();
    expect(screen.getByText('源行号：2')).toBeTruthy();
    expect(screen.getByText('不在当前页')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '导出当前数据' }));
    await waitFor(() => expect(request).toHaveBeenCalledWith('table.export', { path: project.projectPath, datasetId: table.id, destination: 'C:/test/export.gpkg' }));
  });
});

describe('table import preview', () => {
  it('blocks an invalid header row before inspection or import', async () => {
    const { bridge } = fixtureBridge();
    const state = modalState();
    render(<ImportTable bridge={bridge} state={state} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '选择表格文件' }));
    await screen.findByLabelText('表头行');
    fireEvent.change(screen.getByLabelText('表头行'), { target: { value: '1001' } });
    expect(screen.getByRole('alert')).toHaveProperty('textContent', '表头行必须为 1 至 1000 的整数。');
    expect(screen.getByRole('button', { name: '导入表格' }).hasAttribute('disabled')).toBe(true);
    expect(state.inspectTable).not.toHaveBeenCalled();
  });

  it('passes explicit CSV settings, including an actual tab delimiter, to preview and import', async () => {
    const { bridge } = fixtureBridge();
    const state = modalState();
    const close = vi.fn();
    render(<ImportTable bridge={bridge} state={state} onClose={close} />);
    fireEvent.click(screen.getByRole('button', { name: '选择表格文件' }));
    await waitFor(() => expect(state.inspectTable).toHaveBeenCalledWith(tableOptions));
    fireEvent.change(screen.getByLabelText('分隔符'), { target: { value: '\t' } });
    fireEvent.change(screen.getByLabelText('表格字符编码'), { target: { value: 'GBK' } });
    fireEvent.change(screen.getByLabelText('表头行'), { target: { value: '2' } });
    const options = { ...tableOptions, encoding: 'GBK', delimiter: '\t', headerRow: 2 };
    await waitFor(() => expect(state.inspectTable).toHaveBeenLastCalledWith(options));
    await waitFor(() => expect(screen.getByRole('button', { name: '导入表格' }).hasAttribute('disabled')).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: '导入表格' }));
    await waitFor(() => expect(state.importTable).toHaveBeenCalledWith(options));
    expect(close).toHaveBeenCalledOnce();
  });

  it('requires explicit XLSX sheet selection after a sheets-only inspection response', async () => {
    const { bridge } = fixtureBridge();
    bridge.selectTableSource = vi.fn(async () => 'C:/test/points.xlsx');
    const inspectTable = vi.fn(async (options: TableOptions) => ({ ...inspection, sourcePath: options.sourcePath, driver: 'XLSX' as const, sheets: ['First', 'Second'], sheet: options.sheet, columns: options.sheet ? inspection.columns : [], rows: options.sheet ? inspection.rows : [] }));
    const state = modalState({ inspectTable });
    render(<ImportTable bridge={bridge} state={state} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '选择表格文件' }));
    await waitFor(() => expect(inspectTable).toHaveBeenCalledWith({ ...tableOptions, sourcePath: 'C:/test/points.xlsx', sheet: null, encoding: null, delimiter: null }));
    expect(screen.getByLabelText('工作表')).toHaveProperty('value', '');
    expect(screen.getByRole('button', { name: '导入表格' }).hasAttribute('disabled')).toBe(true);
    expect(screen.queryByRole('region', { name: '表格预览' })).toBeNull();
    fireEvent.change(screen.getByLabelText('工作表'), { target: { value: 'Second' } });
    await waitFor(() => expect(screen.getByRole('button', { name: '导入表格' }).hasAttribute('disabled')).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: '导入表格' }));
    await waitFor(() => expect(state.importTable).toHaveBeenCalledWith({ ...tableOptions, sourcePath: 'C:/test/points.xlsx', sheet: 'Second', encoding: null, delimiter: null }));
  });

  it.each(['response', 'engine error'])('discards an obsolete preview %s after parser options change', async (kind) => {
    const { bridge } = fixtureBridge();
    const older = deferred<TableInspection>();
    const inspectTable = vi.fn().mockReturnValueOnce(older.promise).mockResolvedValue({ ...inspection, rows: [{ sourceRow: 3, values: { code: 'new preview', x: '1', y: '2' } }] });
    const state = modalState({ inspectTable });
    render(<ImportTable bridge={bridge} state={state} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '选择表格文件' }));
    await waitFor(() => expect(inspectTable).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByLabelText('表头行'), { target: { value: '2' } });
    expect(await screen.findByText('new preview')).toBeTruthy();
    await act(async () => { if (kind === 'response') older.resolve(inspection); else older.reject({ message: 'obsolete failure', data: { kind: 'ENGINE_TIMEOUT' } }); });
    expect(screen.getByText('new preview')).toBeTruthy();
    expect(screen.queryByText('001')).toBeNull();
    expect(state.handleFailure).not.toHaveBeenCalled();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('catches a synchronous inspection guard error and blocks import', async () => {
    const { bridge } = fixtureBridge();
    const state = modalState({ inspectTable: vi.fn(() => { throw new Error('Project was closed'); }) });
    render(<ImportTable bridge={bridge} state={state} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '选择表格文件' }));
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Project was closed');
    expect(screen.getByRole('button', { name: '导入表格' }).hasAttribute('disabled')).toBe(true);
  });

  it('ignores a late engine failure after the import dialog unmounts', async () => {
    const { bridge } = fixtureBridge();
    const pending = deferred<TableInspection>();
    const state = modalState({ inspectTable: vi.fn(() => pending.promise) });
    const { unmount } = render(<ImportTable bridge={bridge} state={state} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '选择表格文件' }));
    await waitFor(() => expect(state.inspectTable).toHaveBeenCalledOnce());
    unmount();
    await act(async () => pending.reject({ message: 'obsolete timeout', data: { kind: 'ENGINE_TIMEOUT' } }));
    expect(state.handleFailure).not.toHaveBeenCalled();
  });
});

describe('point generation form', () => {
  it('starts with empty X/Y/CRS and only submits explicit values', async () => {
    const state = modalState();
    const close = vi.fn();
    render(<GeneratePoints dataset={table} state={state} onClose={close} />);
    const dialog = within(screen.getByRole('dialog', { name: '生成点数据' }));
    expect(dialog.getByLabelText('X 字段')).toHaveProperty('value', '');
    expect(dialog.getByLabelText('Y 字段')).toHaveProperty('value', '');
    expect(dialog.getByLabelText('点数据来源 CRS')).toHaveProperty('value', '');
    const submit = dialog.getByRole('button', { name: '生成点图层' });
    expect(submit.hasAttribute('disabled')).toBe(true);
    fireEvent.change(dialog.getByLabelText('X 字段'), { target: { value: 'x' } });
    fireEvent.change(dialog.getByLabelText('Y 字段'), { target: { value: 'y' } });
    expect(submit.hasAttribute('disabled')).toBe(true);
    fireEvent.change(dialog.getByLabelText('点数据来源 CRS'), { target: { value: ' EPSG:4326 ' } });
    fireEvent.change(dialog.getByLabelText('Y 字段'), { target: { value: 'x' } });
    expect(submit.hasAttribute('disabled')).toBe(true);
    expect(dialog.getByRole('alert')).toHaveProperty('textContent', 'X 与 Y 必须选择不同字段。');
    fireEvent.change(dialog.getByLabelText('Y 字段'), { target: { value: 'y' } });
    fireEvent.click(submit);
    await waitFor(() => expect(state.generatePoints).toHaveBeenCalledWith(table.id, 'x', 'y', 'EPSG:4326'));
    expect(close).toHaveBeenCalledOnce();
  });
});
