import { describe, expect, it, vi } from 'vitest';
import type { RasterRenderResult } from '../../../shared/contracts';
import type { DesktopBridge } from './bridge';
import { createRasterRenderer, rasterFrame, type RasterImageRequest } from './raster-display';

function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((accept, fail) => { resolve = accept; reject = fail; }); return { promise, resolve, reject }; }
const input: RasterImageRequest = { path: 'C:/project/project.spa', datasetId: 'raster-1', version: 'v1', bbox: [0, 0, 1000, 500], width: 1000, height: 500, style: { mode: 'gray', bands: [1], ranges: [[0, 100]], resampling: 'nearest' } };
const output = (request = input): RasterRenderResult => ({ datasetId: request.datasetId, version: request.version, bbox: request.bbox, width: request.width, height: request.height, dataCrs: 'EPSG:3857', mimeType: 'image/png', imageBase64: 'fixture', resampling: 'nearest' });
const bridge = (request: unknown) => ({ request } as DesktopBridge);

describe('raster display requests', () => {
  it('keeps one in-flight render and only the most recent pending frame/style', async () => {
    const pending = deferred<RasterRenderResult>();
    const request = vi.fn().mockReturnValueOnce(pending.promise).mockImplementation(async (_method, params) => output({ ...params, version: 'v1' }));
    const callbacks = { result: vi.fn(), error: vi.fn(), busy: vi.fn() };
    const queue = createRasterRenderer(bridge(request), callbacks);
    queue.push(input);
    for (let index = 1; index <= 100; index++) queue.push({ ...input, bbox: [index, 0, index + 1000, 500], style: { ...input.style, ranges: [[index, index + 100]] } });
    expect(request).toHaveBeenCalledTimes(1);
    pending.resolve(output());
    await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(2));
    expect(request).toHaveBeenLastCalledWith('raster.render', { path: input.path, datasetId: input.datasetId, bbox: [100, 0, 1100, 500], width: 1000, height: 500, style: { ...input.style, ranges: [[100, 200]] } });
    await vi.waitFor(() => expect(callbacks.result).toHaveBeenCalledOnce());
    expect(callbacks.result.mock.calls[0][0].bbox).toEqual([100, 0, 1100, 500]);
    expect(callbacks.error).not.toHaveBeenCalled();
    expect(callbacks.busy).toHaveBeenLastCalledWith(false);
    queue.dispose();
  });

  it.each(['clear', 'dispose'] as const)('ignores an obsolete engine failure after %s', async (action) => {
    const pending = deferred<RasterRenderResult>();
    const request = vi.fn(() => pending.promise);
    const callbacks = { result: vi.fn(), error: vi.fn(), busy: vi.fn() };
    const queue = createRasterRenderer(bridge(request), callbacks);
    queue.push(input);
    queue.push({ ...input, width: 500 });
    queue[action]();
    pending.reject(new Error('obsolete engine timeout'));
    await pending.promise.catch(() => undefined);
    await Promise.resolve();
    expect(request).toHaveBeenCalledOnce();
    expect(callbacks.result).not.toHaveBeenCalled();
    expect(callbacks.error).not.toHaveBeenCalled();
  });

  it.each([{ version: 'wrong' }, { dataCrs: 'EPSG:4326' }, { width: 20 }, { bbox: [0, 0, 999, 500] }])('rejects a mismatched raster response %j', async (change) => {
    const callbacks = { result: vi.fn(), error: vi.fn(), busy: vi.fn() };
    const queue = createRasterRenderer(bridge(vi.fn(async () => ({ ...output(), ...change }))), callbacks);
    queue.push(input);
    await vi.waitFor(() => expect(callbacks.error).toHaveBeenCalledOnce());
    expect(callbacks.result).not.toHaveBeenCalled();
  });

  it('caps image dimensions while preserving the exact Mercator extent and aspect ratio', () => {
    expect(rasterFrame([0, 0, 3000, 1500], [3000, 1500])).toEqual({ bbox: [0, 0, 3000, 1500], width: 1024, height: 512 });
    const edge = rasterFrame([-30000000, -1000, 1000, 1000], [3000, 200]);
    expect(edge?.bbox[0]).toBe(-20037508.342789244);
    expect(edge?.width).toBeLessThanOrEqual(1024);
    expect(rasterFrame([0, 0, 1, 1], [0, 0])).toBeNull();
    expect(rasterFrame([1, 0, 0, 1], [100, 100])).toBeNull();
  });
});
