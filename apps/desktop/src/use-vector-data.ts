import { useEffect, useRef, useState } from 'react';
import type { AttributeFilter, AttributePage, FeatureResult, TableDataset, VectorDataset } from '../../../shared/contracts';
import type { DesktopBridge } from './bridge';

export interface AttributeQuery {
  offset: number;
  limit: number;
  sortField: string | null;
  descending: boolean;
  filter: AttributeFilter | null;
}

export const initialAttributeQuery: AttributeQuery = { offset: 0, limit: 200, sortField: null, descending: false, filter: null };

export function useAttributePage(bridge: DesktopBridge, path: string | undefined, dataset: VectorDataset | TableDataset | undefined, query: AttributeQuery, enabled: boolean, onFailure: (cause: unknown) => void) {
  const [page, setPage] = useState<AttributePage | null>(null);
  const [loading, setLoading] = useState(false);
  const sequence = useRef(0);
  const queryKey = JSON.stringify(query);
  useEffect(() => {
    const token = ++sequence.current;
    setPage(null);
    setLoading(false);
    if (!enabled || !path || !dataset) return;
    setLoading(true);
    void bridge.request(dataset.kind === 'table' ? 'table.page' : 'vector.page', { path, datasetId: dataset.id, ...query }).then((result) => {
      if (token !== sequence.current) return;
      if (result.datasetId !== dataset.id || result.version !== dataset.version) throw new Error('属性数据版本不匹配，请刷新工作区。');
      setPage(result);
    }).catch((cause) => { if (token === sequence.current) onFailure(cause); }).finally(() => {
      if (token === sequence.current) setLoading(false);
    });
    return () => { sequence.current += 1; };
  }, [bridge, path, dataset?.id, dataset?.version, dataset?.kind, queryKey, enabled, onFailure]);
  return { page, loading };
}

export function useSelectedFeature(bridge: DesktopBridge, path: string | undefined, dataset: VectorDataset | undefined, featureId: string | null, enabled: boolean, onFailure: (cause: unknown) => void) {
  const [selected, setSelected] = useState<FeatureResult | null>(null);
  const [loading, setLoading] = useState(false);
  const sequence = useRef(0);
  useEffect(() => {
    const token = ++sequence.current;
    setSelected(null);
    setLoading(false);
    if (!enabled || !path || !dataset || featureId === null) return;
    setLoading(true);
    void bridge.request('vector.feature', { path, datasetId: dataset.id, featureId }).then((result) => {
      if (token !== sequence.current) return;
      if (result.datasetId !== dataset.id || result.version !== dataset.version || result.row.id !== featureId) throw new Error('要素响应与当前选择不匹配。');
      setSelected(result);
    }).catch((cause) => { if (token === sequence.current) onFailure(cause); }).finally(() => {
      if (token === sequence.current) setLoading(false);
    });
    return () => { sequence.current += 1; };
  }, [bridge, path, dataset?.id, dataset?.version, featureId, enabled, onFailure]);
  return { selected, loading };
}
