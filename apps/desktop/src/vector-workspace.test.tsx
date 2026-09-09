import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AttributePage, FeatureResult, MapLayer, Project, VectorDataset, Workspace } from '../../../shared/contracts';
import type { DesktopBridge } from './bridge';
import { initialAttributeQuery, useAttributePage, useSelectedFeature } from './use-vector-data';
import { useWorkspace } from './use-workspace';
import { VectorWorkspace } from './components/VectorWorkspace';
import { AttributeTable } from './components/AttributeTable';

vi.mock('./components/VectorMap', () => ({
  VectorMap: ({ onSelect, selected }: { onSelect: (layer: string, id: string) => void; selected: FeatureResult | null }) => <div><button onClick={() => onSelect('layer-2', '2')}>Select second map layer</button><span data-testid="highlight">{selected?.row.id ?? ''}</span></div>,
}));

const project: Project = { id: 'project-1', name: 'Vector test', description: '', schemaVersion: 2, createdAt: '2026-09-09T00:00:00Z', updatedAt: '2026-09-09T00:00:00Z', projectPath: 'C:/test/project.spa', analysisCrs: null, displayCrs: 'EPSG:3857', viewState: { center: [114, 27], zoom: 5 } };
const dataset: VectorDataset = {
  id: 'dataset-1', version: 'version-1', name: 'Land', kind: 'vector',
  source: { path: 'C:/test/land.gpkg', layer: 'land', driver: 'GPKG', fingerprint: 'fixture', encoding: null, assignedCrs: null, crsWkt: 'fixture-crs', metadata: {} },
  relativePath: 'data/land.gpkg', storageLayer: 'features', featureCount: 2, geometryType: 'Polygon', crsWkt: 'fixture-crs', crsAuthority: 'EPSG:4326', bounds: [113, 27, 115, 29], boundsWgs84: [113, 27, 115, 29],
  fields: [{ name: 'name', sourceType: 'String', storageType: 'String', nullable: true, alias: null, width: null, precision: null, metadataStatus: 'partial' }],
  internalIdField: '_id', sourceFidField: '_source_fid', report: { status: 'warning', checks: [], warnings: [], notChecked: ['topology'], counts: {}, validatorVersion: 'fixture' }, createdAt: project.createdAt,
};
const secondDataset = { ...dataset, id: 'dataset-2', name: 'Controls' };
const layers: MapLayer[] = [dataset, secondDataset].map((item, index) => ({ id: `layer-${index + 1}`, datasetId: item.id, name: item.name, visible: true, opacity: 1, color: '#326b54', categoryField: null, categoryColors: {}, order: index }));
const workspace: Workspace = { projectId: project.id, datasets: [dataset, secondDataset], layers, tasks: [] };
const rows = [{ id: '1', values: { name: 'first' } }, { id: '2', values: { name: 'second' } }];
function pageFor(datasetId = dataset.id, offset = 0): AttributePage { return { datasetId, version: dataset.version, fields: dataset.fields, rows, total: 2, offset, limit: 200, hasMore: false, truncated: false }; }
function featureFor(id: string, datasetId = dataset.id): FeatureResult { return { datasetId, version: dataset.version, row: rows.find((row) => row.id === id)!, feature: null, boundsWgs84: id === '1' ? [113, 27, 114, 28] : [114, 28, 115, 29] }; }
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((accept, fail) => { resolve = accept; reject = fail; });
  return { promise, resolve, reject };
}
function fixtureBridge(request: DesktopBridge['request']): DesktopBridge {
  return {
    available: () => true, request, chooseParent: vi.fn(async () => 'C:/test'), chooseProject: vi.fn(async () => project.projectPath),
    chooseVector: vi.fn(async () => 'C:/test/land.gpkg'), chooseGdb: vi.fn(async () => 'C:/test/land.gdb'), chooseExport: vi.fn(async () => 'C:/test/export.gpkg'),
    join: vi.fn(async (...parts) => parts.join('/')), diagnosticDirectory: vi.fn(async () => 'C:/test/cache'), onClose: vi.fn(async () => () => undefined), closeWindow: vi.fn(async () => undefined),
  };
}
afterEach(cleanup);

describe('vector query consistency', () => {
  it('discards an older page after the query changes', async () => {
    const older = deferred<AttributePage>();
    const newer = deferred<AttributePage>();
    const request = vi.fn().mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
    const bridge = fixtureBridge(request as DesktopBridge['request']);
    const onFailure = vi.fn();
    const { result, rerender } = renderHook(({ offset }) => useAttributePage(bridge, project.projectPath, dataset, { ...initialAttributeQuery, offset }, true, onFailure), { initialProps: { offset: 0 } });
    rerender({ offset: 200 });
    await act(async () => newer.resolve(pageFor(dataset.id, 200)));
    expect(result.current.page?.offset).toBe(200);
    await act(async () => older.resolve(pageFor(dataset.id, 0)));
    expect(result.current.page?.offset).toBe(200);
    expect(onFailure).not.toHaveBeenCalled();
  });

  it('discards a page response after switching projects', async () => {
    const older = deferred<AttributePage>();
    const newer = deferred<AttributePage>();
    const bridge = fixtureBridge(vi.fn().mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise) as DesktopBridge['request']);
    const failure = vi.fn();
    const { result, rerender } = renderHook(({ path }) => useAttributePage(bridge, path, dataset, initialAttributeQuery, true, failure), { initialProps: { path: project.projectPath } });
    rerender({ path: 'C:/other/project.spa' });
    await act(async () => older.resolve(pageFor()));
    expect(result.current.page).toBeNull();
    await act(async () => newer.resolve({ ...pageFor(), rows: [] }));
    expect(result.current.page?.rows).toEqual([]);
  });

  it('discards an obsolete feature and ignores a failure after selection is cleared', async () => {
    const older = deferred<FeatureResult>();
    const newer = deferred<FeatureResult>();
    const pending = deferred<FeatureResult>();
    const request = vi.fn().mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise).mockReturnValueOnce(pending.promise);
    const bridge = fixtureBridge(request as DesktopBridge['request']);
    const onFailure = vi.fn();
    const { result, rerender } = renderHook(({ id }: { id: string | null }) => useSelectedFeature(bridge, project.projectPath, dataset, id, true, onFailure), { initialProps: { id: '1' } as { id: string | null } });
    rerender({ id: '2' });
    await act(async () => newer.resolve(featureFor('2')));
    await act(async () => older.resolve(featureFor('1')));
    expect(result.current.selected?.row.id).toBe('2');
    rerender({ id: '1' });
    rerender({ id: null });
    await act(async () => pending.reject(new Error('obsolete failure')));
    expect(result.current.selected).toBeNull();
    expect(onFailure).not.toHaveBeenCalled();
  });

  it('rejects a response from an unexpected dataset version', async () => {
    const bridge = fixtureBridge(vi.fn().mockResolvedValue({ ...pageFor(), version: 'stale' }) as DesktopBridge['request']);
    const onFailure = vi.fn();
    const { result } = renderHook(() => useAttributePage(bridge, project.projectPath, dataset, initialAttributeQuery, true, onFailure));
    await waitFor(() => expect(onFailure).toHaveBeenCalledOnce());
    expect(result.current.page).toBeNull();
  });
});

async function openWorkspace(deferredSecond?: ReturnType<typeof deferred<FeatureResult>>) {
  const request = vi.fn(async (method: string, params: Record<string, unknown> = {}) => {
    switch (method) {
      case 'runtime.info': return { protocolVersion: 2, engineVersion: '0.2.0', pythonVersion: 'test', packaged: false, versions: {}, drivers: {}, logPath: 'test' };
      case 'project.open': return project;
      case 'workspace.get': return workspace;
      case 'vector.page': return pageFor(String(params.datasetId));
      case 'vector.feature': return params.featureId === '2' && deferredSecond ? deferredSecond.promise : featureFor(String(params.featureId), String(params.datasetId));
    }
  });
  const bridge = fixtureBridge(request as DesktopBridge['request']);
  const onFit = vi.fn();
  function Harness() {
    const state = useWorkspace(bridge);
    return <><button disabled={!state.runtime} onClick={() => state.requestAction('open')}>Open test project</button><VectorWorkspace bridge={bridge} state={state} fitRequest={null} onFit={onFit} /></>;
  }
  render(<Harness />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Open test project' }).hasAttribute('disabled')).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'Open test project' }));
  await screen.findByTitle('选择要素 1');
  return { request, onFit };
}

describe('map and attribute selection', () => {
  it('returns to the previous real page after response-size truncation', () => {
    const setQuery = vi.fn();
    const props = { dataset, loading: false, setQuery, selectedId: null, selected: null, onSelect: vi.fn(), onClear: vi.fn(), enabled: true };
    const page = { ...pageFor(), total: 1000, hasMore: true, truncated: true };
    const { rerender } = render(<AttributeTable {...props} page={page} query={initialAttributeQuery} />);
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(setQuery).toHaveBeenLastCalledWith({ ...initialAttributeQuery, offset: 2 });
    rerender(<AttributeTable {...props} page={{ ...page, offset: 2 }} query={{ ...initialAttributeQuery, offset: 2 }} />);
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(setQuery).toHaveBeenLastCalledWith({ ...initialAttributeQuery, offset: 4 });
    rerender(<AttributeTable {...props} page={{ ...page, offset: 4 }} query={{ ...initialAttributeQuery, offset: 4 }} />);
    fireEvent.click(screen.getByRole('button', { name: '上一页' }));
    expect(setQuery).toHaveBeenLastCalledWith({ ...initialAttributeQuery, offset: 2 });
  });

  it('keeps the clicked feature when the map changes the selected layer', async () => {
    const { request } = await openWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: 'Select second map layer' }));
    await waitFor(() => expect(request).toHaveBeenCalledWith('vector.feature', { path: project.projectPath, datasetId: secondDataset.id, featureId: '2' }));
    expect(await screen.findByText('选中 ID：2')).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId('highlight').textContent).toBe('2'));
  });

  it('waits for the matching selected feature before fitting the map', async () => {
    const second = deferred<FeatureResult>();
    const { onFit } = await openWorkspace(second);
    fireEvent.click(screen.getByTitle('选择要素 1'));
    await waitFor(() => expect(onFit).toHaveBeenCalledTimes(1));
    expect(onFit).toHaveBeenLastCalledWith(featureFor('1').boundsWgs84);
    fireEvent.click(screen.getByTitle('选择要素 2'));
    expect(onFit).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('highlight').textContent).toBe('');
    await act(async () => second.resolve(featureFor('2')));
    await waitFor(() => expect(onFit).toHaveBeenCalledTimes(2));
    expect(onFit).toHaveBeenLastCalledWith(featureFor('2').boundsWgs84);
  });
});
