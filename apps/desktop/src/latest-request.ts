export function latestRequest<Input, Output>(request: (input: Input) => Promise<Output>, callbacks: {
  result: (output: Output, input: Input) => void;
  error: (cause: unknown, input: Input) => void;
  busy?: (value: boolean) => void;
}) {
  let revision = 0;
  let running = false;
  let disposed = false;
  let pending: { input: Input; revision: number } | null = null;
  const pump = async () => {
    if (running || disposed || !pending) return;
    const current = pending;
    pending = null;
    running = true;
    callbacks.busy?.(true);
    try {
      const output = await request(current.input);
      if (!disposed && current.revision === revision) callbacks.result(output, current.input);
    } catch (cause) {
      if (!disposed && current.revision === revision) callbacks.error(cause, current.input);
    } finally {
      running = false;
      if (!disposed) {
        if (pending) void pump();
        else callbacks.busy?.(false);
      }
    }
  };
  return {
    push(input: Input) { if (!disposed) { pending = { input, revision: ++revision }; callbacks.busy?.(true); void pump(); } },
    clear() { if (!disposed) { revision += 1; pending = null; callbacks.busy?.(false); } },
    dispose() { disposed = true; revision += 1; pending = null; },
  };
}
