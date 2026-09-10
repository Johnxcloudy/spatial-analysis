const abortError = () => new DOMException('查询已取消', 'AbortError');
export async function retryQueryWarmup<T>(request: () => Promise<T>, signal?: AbortSignal, budgetMs = 15000): Promise<T> {
  const deadline = Date.now() + budgetMs;
  for (;;) {
    if (signal?.aborted) throw abortError();
    try { return await request(); }
    catch (cause) {
      if (signal?.aborted) throw abortError();
      if ((cause as { data?: { kind?: string } })?.data?.kind !== 'query_unready' || Date.now() >= deadline) throw cause;
      await new Promise<void>((resolve, reject) => {
        const abort = () => { clearTimeout(timer); reject(abortError()); };
        const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, Math.min(250, deadline - Date.now()));
        signal?.addEventListener('abort', abort, { once: true });
        if (signal?.aborted) abort();
      });
    }
  }
}
