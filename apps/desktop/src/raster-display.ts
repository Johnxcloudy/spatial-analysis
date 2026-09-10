import type { Bounds, RasterRenderResult, RasterStyle } from '../../../shared/contracts';
import type { DesktopBridge } from './bridge';
import { latestRequest } from './latest-request';

export interface RasterFrame { bbox: Bounds; width: number; height: number }
export interface RasterImageRequest extends RasterFrame { path: string; datasetId: string; version: string; style: RasterStyle; signal?: AbortSignal }

export function rasterFrame(extent: number[], size: number[]): RasterFrame | null {
  if (extent.length !== 4 || size.length !== 2 || ![...extent, ...size].every(Number.isFinite) || size.some((value) => value <= 0) || extent[2] <= extent[0] || extent[3] <= extent[1]) return null;
  const limit = 20037508.342789244;
  const bbox: Bounds = [Math.max(-limit, extent[0]), Math.max(-limit, extent[1]), Math.min(limit, extent[2]), Math.min(limit, extent[3])];
  if (bbox[2] <= bbox[0] || bbox[3] <= bbox[1]) return null;
  const width = size[0] * (bbox[2] - bbox[0]) / (extent[2] - extent[0]);
  const height = size[1] * (bbox[3] - bbox[1]) / (extent[3] - extent[1]);
  const scale = Math.min(1, 1024 / Math.max(width, height));
  return { bbox, width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

export function createRasterRenderer(bridge: DesktopBridge, callbacks: { result: (result: RasterRenderResult) => void; error: (cause: unknown) => void; busy: (value: boolean) => void }) {
  return latestRequest(async (input: RasterImageRequest) => {
    const { version, signal, ...params } = input;
    const result = signal ? await bridge.request('raster.render', params, signal) : await bridge.request('raster.render', params);
    if (result.datasetId !== input.datasetId || result.version !== version || result.dataCrs !== 'EPSG:3857' || result.mimeType !== 'image/png' || result.resampling !== 'nearest' || result.width !== input.width || result.height !== input.height || result.bbox.length !== 4 || result.bbox.some((value, index) => value !== input.bbox[index])) throw new Error('栅格显示响应与当前视窗不匹配，请刷新工作区。');
    return result;
  }, callbacks);
}
