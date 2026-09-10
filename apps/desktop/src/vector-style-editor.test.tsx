import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { MapLayer, VectorCartographySpec, VectorDataset } from '../../../shared/contracts';
import { VectorStyleEditor } from './components/VectorStyleEditor';
import { VectorLegend } from './components/VectorLegend';
import { LayerPanel } from './components/LayerPanel';
import type { WorkspaceState } from './use-workspace';
import userEvent from '@testing-library/user-event';

const dataset = { id: 'land', version: 'sha-land', kind: 'vector', geometryType: 'Polygon', featureCount: 20, fields: [{ name: 'code', alias: null }] } as VectorDataset;
const layer: MapLayer = { id: 'layer', datasetId: 'land', name: '土地', visible: true, opacity: 0.7, color: '#123456', categoryField: null, categoryColors: {}, order: 0 };
afterEach(cleanup);

it('keeps preset/category edits local and applies typed values with the expected revision', async () => {
  const save = vi.fn(async (_changes: unknown) => false);
  render(<VectorStyleEditor dataset={dataset} layer={layer} disabled={false} onSave={save} />);
  fireEvent.click(screen.getByRole('button', { name: '采用 Planning 预设' }));
  fireEvent.change(screen.getByLabelText('专题渲染'), { target: { value: 'code' } });
  fireEvent.click(screen.getByRole('button', { name: '添加类别' }));
  fireEvent.change(screen.getByLabelText('新类别类型'), { target: { value: 'number' } });
  fireEvent.change(screen.getByLabelText('新类别值'), { target: { value: '0' } });
  fireEvent.click(screen.getByRole('button', { name: '添加类别' }));
  expect(save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '应用专题样式' }));
  await waitFor(() => expect(save).toHaveBeenCalledOnce());
  const changes = save.mock.calls[0][0] as unknown as { cartography: VectorCartographySpec; expectedCartographyRevision: number };
  expect(changes.expectedCartographyRevision).toBe(0);
  expect(changes.cartography.revision).toBe(1);
  expect(changes.cartography.input).toEqual({ datasetId: dataset.id, version: dataset.version });
  expect(changes.cartography.renderer.kind === 'categorized' && changes.cartography.renderer.categories.map((entry) => entry.value)).toEqual(['', 0]);
  expect(await screen.findByText('保存未完成，草稿已保留。')).toBeTruthy();
  expect(screen.getByLabelText('类别 1 标签')).toHaveProperty('value', '文字 ""');
});

it('restores a saved spec on reset and restores legacy using the monotonic revision', async () => {
  const save = vi.fn(async (_changes: unknown) => true);
  const spec: VectorCartographySpec = { specVersion: 1, kind: 'vector-layer', revision: 4, input: { datasetId: 'land', version: 'sha-land' }, basePreset: 'publication', presetVersion: 1, symbol: { fillColor: '#aabbcc', strokeColor: '#112233', strokeWidthPt: 0.5, pointRadiusPt: 3 }, renderer: { kind: 'single' }, legend: { visible: true, title: '保存的图例' } };
  render(<VectorStyleEditor dataset={dataset} layer={{ ...layer, cartography: spec, cartographyRevision: 4 }} disabled={false} onSave={save} />);
  fireEvent.change(screen.getByLabelText('专题填色'), { target: { value: '#ffffff' } });
  fireEvent.click(screen.getByRole('button', { name: '重置专题草稿' }));
  expect(screen.getByLabelText('专题填色')).toHaveProperty('value', '#aabbcc');
  expect(save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '恢复原有样式' }));
  await waitFor(() => expect(save).toHaveBeenCalledWith({ cartography: null, expectedCartographyRevision: 4 }));
});

it('keeps an edited stale draft on an external revision change until explicit reset', () => {
  const save = vi.fn(async (_changes: unknown) => false);
  const props = { dataset, disabled: false, onSave: save };
  const { rerender } = render(<VectorStyleEditor {...props} layer={layer} />);
  fireEvent.click(screen.getByRole('button', { name: '采用 Planning 预设' }));
  fireEvent.change(screen.getByLabelText('图例标题'), { target: { value: '我的草稿' } });
  rerender(<VectorStyleEditor {...props} layer={{ ...layer, cartographyRevision: 2 }} />);
  expect(screen.getByLabelText('图例标题')).toHaveProperty('value', '我的草稿');
  expect(screen.getByText('已保存样式发生变化，请重置草稿后再应用。')).toBeTruthy();
  expect(screen.getByRole('button', { name: '应用专题样式' })).toHaveProperty('disabled', true);
});

it('labels legends as configured symbols and separates NULL and other without feature counts', () => {
  const spec: VectorCartographySpec = { specVersion: 1, kind: 'vector-layer', revision: 1, input: { datasetId: 'land', version: 'sha-land' }, basePreset: 'planning', presetVersion: 1, symbol: { fillColor: '#abcdef', strokeColor: '#112233', strokeWidthPt: 1, pointRadiusPt: 3 }, renderer: { kind: 'categorized', field: 'code', categories: [{ value: '', label: '空文字', color: '#aabbcc' }], nullColor: '#888888', nullLabel: '缺失', otherColor: '#dddddd', otherLabel: '其余' }, legend: { visible: true, title: '用地配置' } };
  const { rerender } = render(<VectorLegend layer={{ ...layer, cartography: spec }} geometryType="Polygon" />);
  expect(screen.getByText('用地配置')).toBeTruthy();
  expect(screen.getByText('已配置符号 · 不表示类别存在或完整覆盖')).toBeTruthy();
  expect(screen.getByText('空文字')).toBeTruthy();
  expect(screen.getByText('缺失')).toBeTruthy();
  expect(screen.getByText('其余')).toBeTruthy();
  expect(screen.getByLabelText('空文字 符号').querySelector('rect')?.getAttribute('fill')).toBe('#aabbcc');
  rerender(<VectorLegend layer={{ ...layer, cartography: { ...spec, legend: { ...spec.legend, visible: false } } }} geometryType="Polygon" />);
  expect(screen.queryByText('用地配置')).toBeNull();
});

it('clears an unapplied editor draft when switching projects that preserve layer identities', () => {
  const state = { selectedLayerId: layer.id, runtime: {}, workspace: { projectId: 'original', layers: [layer], datasets: [dataset], tasks: [] } } as unknown as WorkspaceState;
  const { rerender } = render(<LayerPanel state={state} onFit={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '采用 Planning 预设' }));
  fireEvent.change(screen.getByLabelText('图例标题'), { target: { value: '未应用的原项目草稿' } });
  rerender(<LayerPanel state={{ ...state, workspace: { ...state.workspace!, projectId: 'saved-copy' } }} onFit={vi.fn()} />);
  expect(screen.queryByLabelText('图例标题')).toBeNull();
  expect(screen.getByLabelText('分类渲染字段')).toBeTruthy();
});

it.each(['', '😀'.repeat(80)])('edits an RPC-valid label without truncating supplementary Unicode characters', async (savedLabel) => {
  const save = vi.fn(async (_changes: unknown) => false);
  const spec: VectorCartographySpec = { specVersion: 1, kind: 'vector-layer', revision: 4, input: { datasetId: 'land', version: 'sha-land' }, basePreset: 'publication', presetVersion: 1, symbol: { fillColor: '#aabbcc', strokeColor: '#112233', strokeWidthPt: 0.5, pointRadiusPt: 3 }, renderer: { kind: 'categorized', field: 'code', categories: [{ value: '001', label: savedLabel, color: '#aabbcc' }], nullColor: '#888888', nullLabel: '', otherColor: '#dddddd', otherLabel: ' ' }, legend: { visible: true, title: '' } };
  const user = userEvent.setup({ delay: null });
  render(<VectorStyleEditor dataset={dataset} layer={{ ...layer, cartography: spec, cartographyRevision: 4 }} disabled={false} onSave={save} />);
  const editedLabel = `${'😀'.repeat(80)} edit`;
  await user.clear(screen.getByLabelText('类别 1 标签'));
  await user.type(screen.getByLabelText('类别 1 标签'), editedLabel);
  expect(screen.getByLabelText('类别 1 标签')).toHaveProperty('value', editedLabel);
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.getByRole('button', { name: '应用专题样式' })).toHaveProperty('disabled', false);
  fireEvent.click(screen.getByRole('button', { name: '应用专题样式' }));
  await waitFor(() => expect(save).toHaveBeenCalledOnce());
  expect(save).toHaveBeenCalledWith({ cartography: { ...spec, revision: 5, renderer: { ...spec.renderer, categories: [{ value: '001', label: editedLabel, color: '#aabbcc' }] } }, expectedCartographyRevision: 4 });
});

it('generates bounded category labels without splitting a surrogate pair and preserves explicit spaces', () => {
  render(<VectorStyleEditor dataset={dataset} layer={layer} disabled={false} onSave={vi.fn(async () => false)} />);
  fireEvent.click(screen.getByRole('button', { name: '采用 Planning 预设' }));
  fireEvent.change(screen.getByLabelText('专题渲染'), { target: { value: 'code' } });
  fireEvent.change(screen.getByLabelText('新类别值'), { target: { value: `x${'😀'.repeat(120)}` } });
  fireEvent.click(screen.getByRole('button', { name: '添加类别' }));
  expect(screen.getByLabelText('类别 1 标签')).toHaveProperty('value', `文字 "x${'😀'.repeat(115)}`);
  fireEvent.change(screen.getByLabelText('新类别值'), { target: { value: 'spaced' } });
  fireEvent.change(screen.getByLabelText('新类别标签'), { target: { value: '   ' } });
  fireEvent.click(screen.getByRole('button', { name: '添加类别' }));
  expect(screen.getByLabelText('类别 2 标签')).toHaveProperty('value', '   ');
});
