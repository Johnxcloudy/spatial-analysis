import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { EngineMethod, Project, RasterDataset, RasterInspection, RasterSampleResult, RasterStyle, Task, VectorDataset, Workspace } from '../../../shared/contracts';
import App from './App';
import type { DesktopBridge } from './bridge';
import { DatasetDetails } from './components/DatasetDetails';
import { ImportRaster } from './components/ImportRaster';
import { RasterPixels } from './components/RasterPixels';
import { RasterStyleEditor } from './components/RasterStyleEditor';
import { useRasterSample } from './use-raster-sample';
import { useWorkspace, type WorkspaceState } from './use-workspace';

vi.mock('./components/VectorMap', () => ({ VectorMap: ({ onSelectPixel, layers }: { onSelectPixel?: (coordinate: [number, number]) => void; layers: { name: string }[] }) => <div data-testid="mixed-map"><button disabled={!onSelectPixel} onClick={() => onSelectPixel?.([114, 27])}>Sample map pixel</button><span data-testid="map-order">{layers.map((layer) => layer.name).join(',')}</span></div> }));

const methods = ['showModal', 'close'] as const;
const descriptors = methods.map((method) => Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, method));
beforeAll(() => Object.defineProperties(HTMLDialogElement.prototype, { showModal: { configurable: true, value() { this.setAttribute('open', ''); } }, close: { configurable: true, value() { this.removeAttribute('open'); } } }));
afterAll(() => methods.forEach((method, index) => { const descriptor = descriptors[index]; if (descriptor) Object.defineProperty(HTMLDialogElement.prototype, method, descriptor); else Reflect.deleteProperty(HTMLDialogElement.prototype, method); }));
afterEach(cleanup);

const project: Project = { id: 'raster-project', name: 'Raster project', description: '', schemaVersion: 4, createdAt: '2026-09-09T00:00:00Z', updatedAt: '2026-09-09T00:00:00Z', projectPath: 'C:/test/raster/project.spa', analysisCrs: null, displayCrs: 'EPSG:3857', viewState: { center: [114, 27], zoom: 6 } };
const runtime = { protocolVersion: 4, engineVersion: '0.4.0', pythonVersion: 'test', packaged: false, versions: {}, drivers: {}, logPath: 'fixture' };
const style: RasterStyle = { mode: 'gray', bands: [1], ranges: [[0, 100]], resampling: 'nearest' };
const raster: RasterDataset = {
  id: 'raster-1', name: 'Terrain', kind: 'raster', version: 'raster-version', relativePath: 'rasters/raster.tif', createdAt: project.createdAt,
  source: { path: 'C:/test/source.tif', layer: '', driver: 'GTiff', fingerprint: 'original-hash', encoding: null, assignedCrs: null, crsWkt: 'fixture-crs', metadata: {} }, crsWkt: 'fixture-crs', crsAuthority: 'EPSG:4326', bounds: [113, 26, 115, 28], boundsWgs84: [113, 26, 115, 28],
  report: { status: 'warning', checks: [], warnings: [], notChecked: ['positional accuracy'], counts: {}, validatorVersion: 'fixture' },
  raster: { width: 10, height: 12, bandCount: 3, transform: [0.1, 0, 113, 0, -0.1, 28], resolution: [0.1, 0.1], horizontalUnit: 'degree', verticalCrsWkt: null, tags: { AREA_OR_POINT: 'Area' }, tagNamespaces: {}, bands: [1, 2, 3].map((index) => ({ index, dtype: 'float32', description: `Band ${index}`, unit: 'm', scale: 2, offset: 10, noData: -9999, colorInterpretation: 'undefined', maskFlags: ['nodata'], overviews: [2], tags: {}, sampleMin: 0, sampleMax: 100, sampledPixels: 120, validSamplePixels: 119 })) },
};
const vector: VectorDataset = { ...raster, id: 'vector-1', name: 'Boundary', kind: 'vector', storageLayer: 'features', featureCount: 1, geometryType: 'Polygon', fields: [], internalIdField: '_id', sourceFidField: '_fid' };
const inspection: RasterInspection = { sourcePath: raster.source.path, driver: 'GTiff', crsWkt: raster.crsWkt, crsAuthority: raster.crsAuthority, bounds: raster.bounds, boundsWgs84: raster.boundsWgs84, raster: raster.raster, warnings: [] };
const sampled: RasterSampleResult = { datasetId: raster.id, version: raster.version, coordinate: [114, 27], sourceCoordinate: [114, 27], pixel: { row: 2, column: 3 }, inside: true, bands: [{ index: 1, rawValue: '0', value: 10, valid: true, reason: null }, { index: 2, rawValue: '-9999', value: null, valid: false, reason: 'nodata' }, { index: 3, rawValue: '9007199254740993', value: null, valid: true, reason: 'unsafe_integer' }] };
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((accept, fail) => { resolve = accept; reject = fail; }); return { promise, resolve, reject }; }

function fixture(unknown = false) {
  const input = unknown ? { ...raster, crsWkt: null, crsAuthority: null, boundsWgs84: null } : raster;
  let currentProject = project;
  let workspace: Workspace = { projectId: project.id, datasets: [input, vector], layers: [input, vector].map((dataset, index) => ({ id: `layer-${index}`, name: dataset.name, datasetId: dataset.id, visible: true, opacity: 1, color: '#327c58', categoryField: null, categoryColors: {}, order: index, ...(dataset.kind === 'raster' ? { rasterStyle: style } : {}) })), tasks: [] };
  const request = vi.fn(async (method: EngineMethod, params: Record<string, unknown> = {}) => {
    if (method === 'runtime.info') return runtime;
    if (method === 'project.open') return currentProject;
    if (method === 'project.close') return { closed: true };
    if (method === 'project.save') { currentProject = { ...currentProject, ...params } as Project; return currentProject; }
    if (method === 'workspace.get') return workspace;
    if (method === 'raster.inspect') return { ...inspection, crsWkt: input.crsWkt, crsAuthority: input.crsAuthority, boundsWgs84: input.boundsWgs84 };
    if (method === 'raster.sample') return sampled;
    if (method === 'layer.update') { const next = { ...workspace.layers.find((layer) => layer.id === params.layerId)!, ...params.changes as object }; workspace = { ...workspace, layers: workspace.layers.map((layer) => layer.id === next.id ? next : layer) }; return next; }
    if (method === 'layer.reorder') { workspace = { ...workspace, layers: (params.layerIds as string[]).map((id, order) => ({ ...workspace.layers.find((layer) => layer.id === id)!, order })) }; return workspace.layers; }
    if (method === 'raster.import' || method === 'raster.export') { const task: Task = { id: `task-${workspace.tasks.length}`, kind: method === 'raster.import' ? 'import' : 'export', status: 'running', stage: 'reading', completed: null, total: null, createdAt: project.createdAt, updatedAt: project.createdAt, datasetId: input.id, destination: null, error: null }; workspace = { ...workspace, tasks: [task, ...workspace.tasks] }; return task; }
    if (method === 'task.get') return workspace.tasks.find((task) => task.id === params.taskId);
    if (method === 'vector.page') return { datasetId: vector.id, version: vector.version, fields: [], rows: [], total: 0, offset: 0, limit: 200, hasMore: false, truncated: false };
    throw new Error(`Unexpected fixture method ${method}`);
  });
  const bridge: DesktopBridge = { available: () => true, request: request as DesktopBridge['request'], chooseParent: vi.fn(async () => 'C:/test'), chooseProject: vi.fn(async () => project.projectPath), chooseVector: vi.fn(async () => 'C:/test/vector.gpkg'), chooseGdb: vi.fn(async () => 'C:/test/vector.gdb'), selectTableSource: vi.fn(async () => 'C:/test/table.csv'), chooseRaster: vi.fn(async () => raster.source.path), chooseRasterExport: vi.fn(async () => 'C:/test/export.tif'), chooseExport: vi.fn(async () => 'C:/test/export.gpkg'), join: vi.fn(async (...parts) => parts.join('/')), diagnosticDirectory: vi.fn(async () => 'C:/test/cache'), onClose: vi.fn(async () => () => undefined), closeWindow: vi.fn(async () => undefined) };
  return { bridge, request };
}

async function openHook() { const f = fixture(); const hook = renderHook(() => useWorkspace(f.bridge)); await waitFor(() => expect(hook.result.current.runtime).toEqual(runtime)); await act(async () => hook.result.current.requestAction('open')); await waitFor(() => expect(hook.result.current.workspace?.datasets).toHaveLength(2)); return { ...f, ...hook }; }

describe('raster workspace operations', () => {
  it('imports and exports against the active project without assigning a CRS', async () => {
    const { result, request, bridge } = await openHook();
    await act(async () => { await result.current.importRaster(raster.source.path); });
    expect(request).toHaveBeenCalledWith('raster.import', { path: project.projectPath, sourcePath: raster.source.path });
    await act(async () => { await result.current.exportRaster(raster.id, raster.name); });
    expect(bridge.chooseRasterExport).toHaveBeenCalledWith(raster.name);
    expect(request).toHaveBeenCalledWith('raster.export', { path: project.projectPath, datasetId: raster.id, destination: 'C:/test/export.tif' });
  });

  it('retains mixed ordering, opacity and raster style across save and reopen', async () => {
    const { result } = await openHook();
    const rgb: RasterStyle = { mode: 'rgb', bands: [3, 2, 1], ranges: [[-10, 80], [2, 40], [0, 255]], resampling: 'nearest' };
    await act(async () => { await result.current.updateLayer('layer-0', { opacity: 0.5, rasterStyle: rgb }); await result.current.reorderLayers(['layer-1', 'layer-0']); });
    await act(async () => result.current.setDraft({ ...result.current.draft!, description: 'raster draft', viewState: { center: [112, 30], zoom: 7 } }));
    await act(async () => { await result.current.refreshWorkspace(); });
    expect(result.current.draft?.description).toBe('raster draft');
    await act(async () => { await result.current.save(); });
    await act(async () => result.current.requestAction('close'));
    await waitFor(() => expect(result.current.project).toBeNull());
    await act(async () => result.current.requestAction('open'));
    await waitFor(() => expect(result.current.workspace?.layers.map((layer) => layer.id)).toEqual(['layer-1', 'layer-0']));
    expect(result.current.workspace?.layers[1]).toMatchObject({ opacity: 0.5, rasterStyle: rgb });
    expect(result.current.draft).toMatchObject({ description: 'raster draft', viewState: { center: [112, 30], zoom: 7 } });
  });

  it('uses raster pixel queries instead of vector attributes, and keeps vector selection available', async () => {
    const { bridge, request } = fixture();
    render(<App bridge={bridge} />);
    await waitFor(() => expect(screen.getByRole('button', { name: '打开项目' }).hasAttribute('disabled')).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: '打开项目' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Sample map pixel' }).hasAttribute('disabled')).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: 'Sample map pixel' }));
    await waitFor(() => expect(request).toHaveBeenCalledWith('raster.sample', { path: project.projectPath, datasetId: raster.id, coordinate: [114, 27] }));
    expect(await screen.findByText('9007199254740993')).toBeTruthy();
    expect(request.mock.calls.some(([method]) => method.startsWith('vector.'))).toBe(false);
    expect(screen.queryByRole('region', { name: '属性表' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /^Boundary/ }));
    await waitFor(() => expect(request).toHaveBeenCalledWith('vector.page', expect.objectContaining({ datasetId: vector.id })));
    expect(screen.queryByRole('region', { name: '像元信息' })).toBeNull();
  });

  it('keeps unknown-CRS raster metadata and export available but disables map sampling', async () => {
    const { bridge, request } = fixture(true);
    render(<App bridge={bridge} />);
    await waitFor(() => expect(screen.getByRole('button', { name: '打开项目' }).hasAttribute('disabled')).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: '打开项目' }));
    expect(await screen.findByText('来源 CRS 未知，不能按位置查询')).toBeTruthy();
    expect(screen.getByRole('button', { name: '识别像元' }).hasAttribute('disabled')).toBe(true);
    expect(screen.getByRole('button', { name: '定位当前图层' }).hasAttribute('disabled')).toBe(true);
    expect(screen.getByRole('button', { name: '导出当前数据' }).hasAttribute('disabled')).toBe(false);
    expect(request.mock.calls.some(([method]) => method === 'raster.sample')).toBe(false);
  });
});

describe('raster display settings and values', () => {
  it('saves explicit RGB channels and raw ranges, accepting a constant range', async () => {
    const save = vi.fn(async () => true);
    render(<RasterStyleEditor dataset={raster} style={style} disabled={false} onSave={save} />);
    fireEvent.click(screen.getByRole('button', { name: 'RGB' }));
    fireEvent.change(screen.getByLabelText('R 波段'), { target: { value: '3' } });
    fireEvent.change(screen.getByLabelText('R 最小值'), { target: { value: '-10' } });
    fireEvent.change(screen.getByLabelText('R 最大值'), { target: { value: '-10' } });
    fireEvent.change(screen.getByLabelText('G 最小值'), { target: { value: '101' } });
    expect(screen.getByRole('button', { name: '应用栅格显示设置' }).hasAttribute('disabled')).toBe(true);
    fireEvent.change(screen.getByLabelText('G 最小值'), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: '应用栅格显示设置' }));
    expect(save).toHaveBeenCalledWith({ mode: 'rgb', bands: [3, 2, 3], ranges: [[-10, -10], [0, 100], [0, 100]], resampling: 'nearest' });
    expect(screen.getAllByText('119 / 120 有效像元')).toHaveLength(3);
  });

  it('shows raw integer text, zero, NoData validity and scale/offset output separately', () => {
    render(<RasterPixels dataset={raster} result={sampled} loading={false} enabled onClear={vi.fn()} />);
    const rows = screen.getAllByRole('row');
    expect(within(rows[1]).getByText('0')).toBeTruthy();
    expect(within(rows[1]).getByText('10')).toBeTruthy();
    expect(within(rows[2]).getByText('nodata')).toBeTruthy();
    expect(within(rows[2]).getByText('否')).toBeTruthy();
    expect(within(rows[3]).getByText('9007199254740993')).toBeTruthy();
    expect(within(rows[3]).getByText('不可用')).toBeTruthy();
    expect(screen.getByText('行 2 · 列 3（从 0 开始）')).toBeTruthy();
  });

  it('labels raster statistics as sampled and never fabricates vector metadata', () => {
    render(<DatasetDetails dataset={raster} />);
    expect(screen.getAllByText('抽样最小/最大')).toHaveLength(3);
    expect(screen.getAllByText('Scale / Offset')).toHaveLength(3);
    expect(screen.queryByText('要素')).toBeNull();
    expect(screen.queryByText(/字段定义/)).toBeNull();
  });
});

describe('raster response isolation', () => {
  it('reports the current engine error, disables queries during recovery, and resumes with a new project session', async () => {
    const { bridge } = fixture();
    const failure = vi.fn();
    const timeout = { message: 'Engine timed out', data: { kind: 'ENGINE_TIMEOUT' } };
    const request = vi.fn().mockRejectedValueOnce(timeout).mockResolvedValue(sampled);
    bridge.request = request as DesktopBridge['request'];
    const { result, rerender } = renderHook(({ enabled, path }) => useRasterSample(bridge, path, raster, [114, 27], enabled, failure), { initialProps: { enabled: true, path: project.projectPath } });
    await waitFor(() => expect(failure).toHaveBeenCalledWith(timeout));
    expect(result.current.loading).toBe(false);
    rerender({ enabled: false, path: project.projectPath });
    expect(result.current.result).toBeNull();
    expect(request).toHaveBeenCalledOnce();
    rerender({ enabled: true, path: 'C:/test/reopened/project.spa' });
    await waitFor(() => expect(result.current.result).toEqual(sampled));
    expect(request).toHaveBeenLastCalledWith('raster.sample', { path: 'C:/test/reopened/project.spa', datasetId: raster.id, coordinate: [114, 27] });
  });

  it('suppresses a pending sample failure after unmount', async () => {
    const { bridge } = fixture();
    const pending = deferred<RasterSampleResult>();
    bridge.request = vi.fn(() => pending.promise) as DesktopBridge['request'];
    const failure = vi.fn();
    const { unmount } = renderHook(() => useRasterSample(bridge, project.projectPath, raster, [114, 27], true, failure));
    unmount();
    await act(async () => pending.reject({ message: 'late error', data: { kind: 'ENGINE_TIMEOUT' } }));
    expect(failure).not.toHaveBeenCalled();
  });

  it('coalesces pixel clicks and discards prior results and late engine errors', async () => {
    const { bridge } = fixture();
    const old = deferred<RasterSampleResult>();
    const request = vi.fn().mockReturnValueOnce(old.promise).mockImplementation(async (_method, params) => ({ ...sampled, coordinate: params.coordinate }));
    bridge.request = request as DesktopBridge['request'];
    const failure = vi.fn();
    const { result, rerender } = renderHook(({ coordinate }: { coordinate: [number, number] | null }) => useRasterSample(bridge, project.projectPath, raster, coordinate, true, failure), { initialProps: { coordinate: [114, 27] as [number, number] | null } });
    rerender({ coordinate: [114.1, 27] });
    rerender({ coordinate: [114.2, 27] });
    expect(request).toHaveBeenCalledOnce();
    await act(async () => old.reject({ message: 'obsolete', data: { kind: 'ENGINE_TIMEOUT' } }));
    await waitFor(() => expect(result.current.result?.coordinate).toEqual([114.2, 27]));
    expect(request).toHaveBeenCalledTimes(2);
    expect(failure).not.toHaveBeenCalled();
    rerender({ coordinate: null });
    expect(result.current.result).toBeNull();
  });

  it('discards a sample when switching to an unknown-CRS dataset', async () => {
    const { bridge } = fixture();
    const pending = deferred<RasterSampleResult>();
    bridge.request = vi.fn(() => pending.promise) as DesktopBridge['request'];
    const failure = vi.fn();
    const { result, rerender } = renderHook(({ dataset }) => useRasterSample(bridge, project.projectPath, dataset, [114, 27], true, failure), { initialProps: { dataset: raster } });
    rerender({ dataset: { ...raster, id: 'unknown', crsWkt: null, crsAuthority: null, boundsWgs84: null } });
    await act(async () => pending.resolve(sampled));
    expect(result.current.result).toBeNull();
    expect(bridge.request).toHaveBeenCalledOnce();
  });

  it('discards an obsolete raster inspection after choosing another file', async () => {
    const { bridge } = fixture();
    const first = deferred<RasterInspection>();
    const inspectRaster = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue({ ...inspection, raster: { ...raster.raster, width: 999 } });
    const state = { busy: null, needsReopen: false, activeTask: null, error: null, inspectRaster, handleFailure: vi.fn(), importRaster: vi.fn(async () => true) } as unknown as WorkspaceState;
    render(<ImportRaster bridge={bridge} state={state} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '选择栅格文件' }));
    await waitFor(() => expect(inspectRaster).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole('button', { name: '选择栅格文件' }));
    expect(await screen.findByText('999 × 12')).toBeTruthy();
    await act(async () => first.reject({ message: 'obsolete', data: { kind: 'ENGINE_TIMEOUT' } }));
    expect(screen.getByText('999 × 12')).toBeTruthy();
    expect(state.handleFailure).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '导入栅格' }));
    await waitFor(() => expect(state.importRaster).toHaveBeenCalledWith(raster.source.path));
  });
});
