import type { AttributeFilter, TableDataset, VectorDataset } from '../../../shared/contracts';
import type { DesktopBridge } from './bridge';
import { useQueuedQuery } from './use-queued-query';
import { forwardPageFailure } from './page-query-error';

export interface AttributeQuery {
  offset: number;
  limit: number;
  sortField: string | null;
  descending: boolean;
  filter: AttributeFilter | null;
}

export const initialAttributeQuery: AttributeQuery = { offset: 0, limit: 200, sortField: null, descending: false, filter: null };

export function useAttributePage(bridge: DesktopBridge, path: string | undefined, dataset: VectorDataset | TableDataset | undefined, query: AttributeQuery, enabled: boolean, onFailure: (cause: unknown) => void) {
  const { value: page, loading, error, retry } = useQueuedQuery(JSON.stringify([path, dataset?.id, dataset?.version, dataset?.kind, query]), enabled && !!path && !!dataset, async (signal) => {
    const result = await bridge.request(dataset!.kind === 'table' ? 'table.page' : 'vector.page', { path, datasetId: dataset!.id, ...query }, signal);
    if (result.datasetId !== dataset!.id || result.version !== dataset!.version) throw new Error('属性数据版本不匹配，请刷新工作区。');
    return result;
  }, (cause) => forwardPageFailure(cause, onFailure));
  return { page, loading, error, retry };
}

export function useSelectedFeature(bridge: DesktopBridge, path: string | undefined, dataset: VectorDataset | undefined, featureId: string | null, enabled: boolean, onFailure: (cause: unknown) => void) {
  const { value: selected, loading } = useQueuedQuery(JSON.stringify([path, dataset?.id, dataset?.version, featureId]), enabled && !!path && !!dataset && featureId !== null, async (signal) => {
    const result = await bridge.request('vector.feature', { path, datasetId: dataset!.id, featureId }, signal);
    if (result.datasetId !== dataset!.id || result.version !== dataset!.version || result.row.id !== featureId) throw new Error('要素响应与当前选择不匹配。');
    return result;
  }, onFailure);
  return { selected, loading };
}
