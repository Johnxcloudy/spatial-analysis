import { afterEach, expect, it, vi } from 'vitest';
import { retryQueryWarmup } from './query-warmup';
afterEach(() => vi.useRealTimers());
it('retries only temporary warmup and stops on abort or finite deadline', async () => {
  vi.useFakeTimers();
  const unavailable = { data: { kind: 'query_unready' } };
  const request = vi.fn().mockRejectedValueOnce(unavailable).mockResolvedValue(42);
  const result = retryQueryWarmup(request);
  await vi.advanceTimersByTimeAsync(250);
  expect(await result).toBe(42);
  const controller = new AbortController();
  const waiting = retryQueryWarmup(vi.fn().mockRejectedValue(unavailable), controller.signal);
  const aborted = expect(waiting).rejects.toMatchObject({ name: 'AbortError' });
  controller.abort();
  await aborted;
  const forever = vi.fn().mockRejectedValue(unavailable);
  const limited = expect(retryQueryWarmup(forever, undefined, 500)).rejects.toEqual(unavailable);
  await vi.advanceTimersByTimeAsync(500);
  await limited;
  expect(forever).toHaveBeenCalledTimes(3);
});
it('does not retry resource failures', async () => {
  const error = { data: { kind: 'query_timeout' } };
  const request = vi.fn().mockRejectedValue(error);
  await expect(retryQueryWarmup(request)).rejects.toBe(error);
  expect(request).toHaveBeenCalledOnce();
});
