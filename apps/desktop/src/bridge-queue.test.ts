import { afterEach, expect, it, vi } from 'vitest';
vi.mock('@tauri-apps/api/core', () => ({ isTauri: () => true, invoke: vi.fn() }));
import { invoke } from '@tauri-apps/api/core';
import { engineRequest } from './bridge';
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks(); });

it('normalizes a native JSON-string warmup error before bounded retry', async () => {
  vi.useFakeTimers();
  const result = { datasetId: 'dataset', rows: [] };
  vi.mocked(invoke).mockRejectedValueOnce(JSON.stringify({ code: -32000, message: 'warming', data: { kind: 'query_unready' } })).mockResolvedValueOnce(result);
  const request = engineRequest('vector.page', { datasetId: 'dataset' });
  const outcome = expect(request).resolves.toEqual(result);
  await vi.advanceTimersByTimeAsync(250);
  await outcome;
  expect(invoke).toHaveBeenCalledTimes(2);
});
