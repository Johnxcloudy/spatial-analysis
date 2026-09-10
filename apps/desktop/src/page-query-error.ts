import { normalizeError } from './bridge';

const recoverable = new Set(['query_timeout', 'query_unready', 'REQUEST_QUEUE_FULL', 'REQUEST_QUEUE_PREEMPTED']);

/** Page failures are local; engine/session failures retain workspace recovery. */
export function forwardPageFailure(cause: unknown, onFailure: (cause: unknown) => void) {
  if (!recoverable.has(normalizeError(cause).data?.kind ?? '')) onFailure(cause);
}

export function pageFailureText(cause: unknown) {
  const error = normalizeError(cause);
  const context = error.data?.kind === 'query_timeout' ? '本次读取超时，数据仍保留，可重试。' : '';
  return `${context}${error.message}${error.data?.kind ? ` (${error.data.kind})` : ''}`;
}
