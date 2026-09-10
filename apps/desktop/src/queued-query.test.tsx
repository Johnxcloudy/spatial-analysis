import { act, renderHook, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { useQueuedQuery } from './use-queued-query';

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
