import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useQueuedQuery } from './use-queued-query';
afterEach(cleanup);

it('exposes failure and retries only on an explicit action with a new signal', async () => {
  const timeout = { message: 'budget exceeded', data: { kind: 'query_timeout' } };
  let complete!: (value: number) => void;
  const request = vi.fn().mockRejectedValueOnce(timeout).mockImplementationOnce(() => new Promise<number>((resolve) => { complete = resolve; }));
  const { result } = renderHook(() => useQueuedQuery('same', true, request, vi.fn()));
  await waitFor(() => expect(result.current.error).toBe(timeout));
  expect(request).toHaveBeenCalledTimes(1);
  const oldSignal = request.mock.calls[0][0];
  act(() => { result.current.retry(); result.current.retry(); });
  expect(request).toHaveBeenCalledTimes(2);
  expect(result.current.error).toBeNull();
  expect(result.current.loading).toBe(true);
  expect(oldSignal.aborted).toBe(true);
  expect(request.mock.calls[1][0]).not.toBe(oldSignal);
  await act(async () => complete(42));
  expect(result.current.value).toBe(42);
  expect(result.current.error).toBeNull();
});

it('does not replay retained retry callbacks after identity changes, hide or unmount', async () => {
  const request = vi.fn(async () => 1);
  const { result, rerender, unmount } = renderHook(({ key, enabled }) => useQueuedQuery(key, enabled, request, vi.fn()), { initialProps: { key: 'old', enabled: true } });
  await waitFor(() => expect(result.current.value).toBe(1));
  const stale = result.current.retry;
  rerender({ key: 'new', enabled: true });
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  act(() => stale());
  expect(request).toHaveBeenCalledTimes(2);
  const hidden = result.current.retry;
  rerender({ key: 'new', enabled: false });
  act(() => hidden());
  expect(request).toHaveBeenCalledTimes(2);
  rerender({ key: 'new', enabled: true });
  await waitFor(() => expect(request).toHaveBeenCalledTimes(3));
  act(() => hidden());
  expect(request).toHaveBeenCalledTimes(3);
  const disposed = result.current.retry;
  unmount();
  act(() => disposed());
  expect(request).toHaveBeenCalledTimes(3);
});

it('coalesces requests to latest input and stops hidden queries', async () => {
  const release: ((value: number) => void)[] = [];
  const request = vi.fn(() => new Promise<number>((resolve) => release.push(resolve)));
  const failure = vi.fn();
  const { result, rerender } = renderHook(({ key, enabled }) => useQueuedQuery(String(key), enabled, request, failure), { initialProps: { key: 0, enabled: true } });
  rerender({ key: 1, enabled: true });
  rerender({ key: 2, enabled: true });
  expect(request).toHaveBeenCalledTimes(1);
  await act(async () => release[0](0));
  expect(result.current.value).toBeNull();
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  await act(async () => release[1](2));
  expect(result.current.value).toBe(2);
  rerender({ key: 3, enabled: false });
  expect(request).toHaveBeenCalledTimes(2);
  expect(result.current.loading).toBe(false);
});

it('discards a retried query late error when a new identity is waiting', async () => {
  let rejectOld!: (cause: unknown) => void;
  const request = vi.fn().mockRejectedValueOnce(new Error('first')).mockImplementationOnce(() => new Promise<number>((_resolve, reject) => { rejectOld = reject; })).mockResolvedValueOnce(9);
  const failure = vi.fn();
  const { result, rerender } = renderHook(({ key }) => useQueuedQuery(key, true, request, failure), { initialProps: { key: 'old' } });
  await waitFor(() => expect(result.current.error).not.toBeNull());
  act(() => result.current.retry());
  expect(request).toHaveBeenCalledTimes(2);
  rerender({ key: 'new' });
  expect(result.current.error).toBeNull();
  expect(request.mock.calls[1][0].aborted).toBe(true);
  await act(async () => rejectOld(new Error('obsolete retry')));
  await waitFor(() => expect(result.current.value).toBe(9));
  expect(failure).toHaveBeenCalledTimes(1);
  expect(request).toHaveBeenCalledTimes(3);
});
