import { useEffect, useRef, useState } from 'react';
import type { RasterDataset, RasterSampleResult } from '../../../shared/contracts';
import type { DesktopBridge } from './bridge';
import { latestRequest } from './latest-request';

interface SampleRequest { path: string; datasetId: string; version: string; coordinate: [number, number] }

export function useRasterSample(bridge: DesktopBridge, path: string | undefined, dataset: RasterDataset | undefined, coordinate: [number, number] | null, enabled: boolean, onFailure: (cause: unknown) => void) {
  const [result, setResult] = useState<RasterSampleResult | null>(null);
  const [loading, setLoading] = useState(false);
  const queue = useRef<ReturnType<typeof latestRequest<SampleRequest, RasterSampleResult>> | null>(null);
  useEffect(() => {
    queue.current = latestRequest(async (request: SampleRequest) => {
      const response = await bridge.request('raster.sample', { path: request.path, datasetId: request.datasetId, coordinate: request.coordinate });
      if (response.datasetId !== request.datasetId || response.version !== request.version || response.coordinate.length !== 2 || response.coordinate.some((value, index) => value !== request.coordinate[index])) throw new Error('像元响应与当前查询不匹配，请刷新工作区。');
      return response;
    }, { result: (response) => setResult(response), error: (cause) => onFailure(cause), busy: setLoading });
    return () => queue.current?.dispose();
  }, [bridge, onFailure]);
  useEffect(() => {
    setResult(null);
    if (enabled && path && dataset?.crsWkt && coordinate) queue.current?.push({ path, datasetId: dataset.id, version: dataset.version, coordinate });
    else queue.current?.clear();
  }, [bridge, onFailure, path, dataset?.id, dataset?.version, dataset?.crsWkt, coordinate?.[0], coordinate?.[1], enabled]);
  const current = enabled && dataset?.crsWkt && result?.datasetId === dataset.id && result?.version === dataset.version && coordinate && result.coordinate.every((value, index) => value === coordinate[index]) ? result : null;
  return { result: current, loading };
}
