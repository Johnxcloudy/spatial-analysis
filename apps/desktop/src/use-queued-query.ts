import { useEffect, useRef, useState } from 'react';
import { latestRequest } from './latest-request';

/** Retains one active query and only the newest pending query across renders. */
export function useQueuedQuery<T>(key: string, enabled: boolean, request: (signal: AbortSignal) => Promise<T>, onFailure: (cause: unknown) => void) {
  const [value, setValue] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const failure = useRef(onFailure);
  failure.current = onFailure;
  const queue = useRef<ReturnType<typeof latestRequest<() => Promise<T>, T>> | null>(null);
  useEffect(() => {
    const current = latestRequest<() => Promise<T>, T>((read) => read(), { result: (result) => setValue(result), error: (cause) => failure.current(cause), busy: setLoading });
    queue.current = current;
    return () => { current.dispose(); queue.current = null; };
  }, []);
  useEffect(() => {
    setValue(null);
    const controller = new AbortController();
    if (enabled) queue.current?.push(() => request(controller.signal));
    else queue.current?.clear();
    return () => { controller.abort(); queue.current?.clear(); };
  }, [key, enabled]);
  return { value, loading };
}
