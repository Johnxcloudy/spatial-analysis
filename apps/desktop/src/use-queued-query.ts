import { useEffect, useRef, useState } from 'react';
import { latestRequest } from './latest-request';

/** Retains one active query and only the newest pending query across renders. */
export function useQueuedQuery<T>(key: string, enabled: boolean, request: (signal: AbortSignal) => Promise<T>, onFailure: (cause: unknown) => void) {
  const identity = useRef({ key, enabled });
  if (identity.current.key !== key || identity.current.enabled !== enabled) identity.current = { key, enabled };
  const currentIdentity = identity.current;
  const [settled, setSettled] = useState<{ identity: typeof currentIdentity; value: T | null; error: unknown } | null>(null);
  const [loading, setLoading] = useState(false);
  const busy = useRef(false);
  const [revision, setRevision] = useState(0);
  const failure = useRef(onFailure);
  failure.current = onFailure;
  type Read = { identity: typeof currentIdentity; run: () => Promise<T> };
  const queue = useRef<ReturnType<typeof latestRequest<Read, T>> | null>(null);
  useEffect(() => {
    const current = latestRequest<Read, T>((read) => read.run(), {
      result: (value, read) => { if (identity.current === read.identity) setSettled({ identity: read.identity, value, error: null }); },
      error: (error, read) => { if (identity.current === read.identity) { setSettled({ identity: read.identity, value: null, error }); failure.current(error); } },
      busy: (value) => { busy.current = value; setLoading(value); },
    });
    queue.current = current;
    return () => { current.dispose(); queue.current = null; };
  }, []);
  useEffect(() => {
    setSettled(null);
    const controller = new AbortController();
    if (enabled) queue.current?.push({ identity: currentIdentity, run: () => request(controller.signal) });
    else queue.current?.clear();
    return () => { controller.abort(); queue.current?.clear(); };
  }, [key, enabled, revision]);
  const retry = () => {
    if (!queue.current || identity.current !== currentIdentity || !enabled || busy.current) return;
    busy.current = true;
    setLoading(true);
    setSettled(null);
    setRevision((value) => value + 1);
  };
  const current = enabled && settled?.identity === currentIdentity ? settled : null;
  return { value: current?.value ?? null, error: current?.error ?? null, loading: enabled && loading, retry };
}
