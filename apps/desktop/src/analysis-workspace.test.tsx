import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { AnalysisResultPage, VectorDataset } from '../../../shared/contracts';
import type { DesktopBridge } from './bridge';
import type { WorkspaceState } from './use-workspace';
import { AnalysisWorkspace } from './components/AnalysisWorkspace';

afterEach(cleanup);
const polygon = (id: string): VectorDataset => ({ id, name: id, kind: 'vector', version: `version-${id}`, geometryType: 'Polygon', crsWkt: 'WKT', fields: [{ name: 'code', storageType: 'String', sourceType: 'String' }, { name: 'area', storageType: 'Real', sourceType: 'Real' }], source: { driver: 'GPKG' } } as VectorDataset);
function fixture() {
  const state = { native: true, runtime: {}, project: { id: 'p', projectPath: 'C:/project.spa', analysisCrs: null }, workspace: { datasets: [polygon('land'), polygon('boundary')], layers: [], tasks: [] }, runAnalysis: vi.fn(), handleFailure: vi.fn(), exportVector: vi.fn(), exportAnalysisCsv: vi.fn(), setSelectedLayerId: vi.fn() } as unknown as WorkspaceState;
  const bridge = { request: vi.fn() } as unknown as DesktopBridge;
  return { state, bridge };
}
it('requires explicit CRS reason and standard and submits complete snapshots with strict policy', () => {
  const { state, bridge } = fixture();
  render(<AnalysisWorkspace state={state} bridge={bridge} visible onShowMap={() => {}} />);
  fireEvent.change(screen.getByLabelText('用地数据集'), { target: { value: 'land' } });
  fireEvent.change(screen.getByLabelText('叠加数据集'), { target: { value: 'boundary' } });
  fireEvent.change(screen.getByLabelText('左侧地类字段'), { target: { value: 'code' } });
  expect(screen.queryByRole('option', { name: 'area' })).toBeNull();
  expect((screen.getByRole('button', { name: '开始分析' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('成果名称'), { target: { value: '分类结果' } });
  fireEvent.change(screen.getByLabelText('分类标准及版本'), { target: { value: '自定义2026' } });
  fireEvent.change(screen.getByLabelText('分析投影'), { target: { value: 'EPSG:4547' } });
  fireEvent.change(screen.getByLabelText('投影适用理由'), { target: { value: '研究区位于中央经线附近' } });
  fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
  expect(state.runAnalysis).toHaveBeenCalledWith({ operation: 'clip', name: '分类结果', inputDatasetId: 'land', overlayDatasetId: 'boundary', inputClassField: 'code', overlayClassField: null, classificationStandard: '自定义2026', analysisCrs: 'EPSG:4547', crsReason: '研究区位于中央经线附近' });
  expect(screen.getByText(/不自动修复/)).toBeTruthy();
});
it('loads registered results on reopen only when visible, with a bounded page', async () => {
  const { state, bridge } = fixture();
  state.workspace!.datasets.push({ ...polygon('result'), source: { ...polygon('result').source, driver: 'SpatialAnalysis' } });
  vi.mocked(bridge.request).mockRejectedValue(new Error('fixture read'));
  const { rerender } = render(<AnalysisWorkspace state={state} bridge={bridge} visible={false} onShowMap={() => {}} />);
  expect(bridge.request).not.toHaveBeenCalled();
  rerender(<AnalysisWorkspace state={state} bridge={bridge} visible onShowMap={() => {}} />);
  await waitFor(() => expect(bridge.request).toHaveBeenCalledWith('analysis.result', { path: 'C:/project.spa', datasetId: 'result', offset: 0, limit: 100 }, expect.any(AbortSignal)));
});

it('paginates full statistics, distinguishes NULL and text and exports the registered result', async () => {
  const { state, bridge } = fixture();
  const result = { ...polygon('result'), source: { ...polygon('result').source, driver: 'SpatialAnalysis' } };
  state.workspace!.datasets.push(result);
  state.workspace!.layers.push({ id: 'result-layer', datasetId: 'result' } as never);
  const row = { inputClass: null, overlayClass: null, featureCount: 1, areaM2: 100, areaHa: 0.01, areaMu: 0.15, studyRatio: 0.5, coverageRatio: 1 };
  const page = { datasetId: result.id, version: result.version, offset: 0, limit: 100, total: 101, hasMore: true, rows: Array.from({ length: 100 }, (_, index) => ({ ...row, inputClass: index === 0 ? null : index === 1 ? 'NULL' : String(index).padStart(3, '0') })), record: { resultDatasetId: result.id, operation: 'clip', studyAreaM2: 200, coveredAreaM2: 100, uncoveredAreaM2: 100, recordAreaM2: 100, outputFeatureCount: 100, boundaryContactCount: 0, coverageExplanation: '互斥图斑的覆盖面积', classificationStandard: '自定义', crsReason: '适用', analysisCrsAuthority: 'EPSG:4547' } } as AnalysisResultPage;
  const lastPage = { ...page, offset: 100, rows: [{ ...row, inputClass: '' }], hasMore: false };
  vi.mocked(bridge.request).mockResolvedValueOnce(page).mockRejectedValueOnce({ message: 'statistics time budget', data: { kind: 'query_timeout' } }).mockResolvedValueOnce(lastPage).mockResolvedValueOnce(lastPage);
  const onShowMap = vi.fn();
  render(<AnalysisWorkspace state={state} bridge={bridge} visible onShowMap={onShowMap} />);
  expect(await screen.findByText('未分类（NULL）')).toBeTruthy();
  expect(screen.getByText('"NULL"')).toBeTruthy();
  expect(screen.getByText('"002"')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '统计下一页' }));
  expect(await screen.findByText('统计读取失败')).toBeTruthy();
  expect(state.handleFailure).not.toHaveBeenCalled();
  expect(bridge.request).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole('button', { name: '重试当前统计页' }));
  expect(await screen.findByText('""')).toBeTruthy();
  expect(bridge.request).toHaveBeenLastCalledWith('analysis.result', expect.objectContaining({ offset: 100, limit: 100 }), expect.any(AbortSignal));
  expect(vi.mocked(bridge.request).mock.calls[2].slice(0, 2)).toEqual(vi.mocked(bridge.request).mock.calls[1].slice(0, 2));
  fireEvent.click(screen.getByRole('button', { name: '刷新统计' }));
  expect(await screen.findByText('""')).toBeTruthy();
  expect(bridge.request).toHaveBeenCalledTimes(4);
  fireEvent.click(screen.getByRole('button', { name: '导出统计 CSV' }));
  expect(state.exportAnalysisCsv).toHaveBeenCalledWith('result', 'result');
  fireEvent.click(screen.getByRole('button', { name: '导出成果 GeoPackage' }));
  expect(state.exportVector).toHaveBeenCalledWith('result', 'result');
  fireEvent.click(screen.getByRole('button', { name: '查看成果图斑' }));
  expect(state.setSelectedLayerId).toHaveBeenCalledWith('result-layer');
  expect(onShowMap).toHaveBeenCalledOnce();
});

it('ignores late statistics failures on hide and preserves engine recovery on the current page', async () => {
  const { state, bridge } = fixture();
  state.workspace!.datasets.push({ ...polygon('result'), source: { ...polygon('result').source, driver: 'SpatialAnalysis' } });
  let reject!: (cause: unknown) => void;
  const disconnected = { message: 'gone', data: { kind: 'ENGINE_DISCONNECTED' } };
  vi.mocked(bridge.request).mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail; })).mockRejectedValueOnce(disconnected);
  const { rerender } = render(<AnalysisWorkspace state={state} bridge={bridge} visible onShowMap={vi.fn()} />);
  rerender(<AnalysisWorkspace state={state} bridge={bridge} visible={false} onShowMap={vi.fn()} />);
  await act(async () => reject(new Error('obsolete')));
  expect(state.handleFailure).not.toHaveBeenCalled();
  expect(screen.queryByRole('alert')).toBeNull();
  rerender(<AnalysisWorkspace state={state} bridge={bridge} visible onShowMap={vi.fn()} />);
  await waitFor(() => expect(state.handleFailure).toHaveBeenCalledWith(disconnected));
  state.needsReopen = true;
  rerender(<AnalysisWorkspace state={state} bridge={bridge} visible onShowMap={vi.fn()} />);
  expect((screen.getByRole('button', { name: '刷新统计' }) as HTMLButtonElement).disabled).toBe(true);
  expect(bridge.request).toHaveBeenCalledTimes(2);
});
