/** One transport request at a time, with a bounded waiting room. */
export function createRequestQueue(capacity = 32) {
  const pending: { priority: 'urgent' | 'control' | 'display'; execute: () => Promise<void>; reject: (cause: unknown) => void }[] = [];
  let running = false;
  const pump = () => {
    if (running || !pending.length) return;
    const urgent = pending.findIndex((item) => item.priority === 'urgent');
    const control = urgent >= 0 ? urgent : pending.findIndex((item) => item.priority === 'control');
    const next = pending.splice(control < 0 ? 0 : control, 1)[0];
    running = true;
    void next.execute().finally(() => { running = false; pump(); });
  };
  return {
    run<T>(priority: 'urgent' | 'control' | 'display', request: () => Promise<T>): Promise<T> {
      if (pending.length >= capacity) {
        const display = pending.findIndex((item) => item.priority === 'display');
        const removable = priority === 'urgent' ? display >= 0 ? display : pending.length - 1 : priority === 'control' ? display : -1;
        if (removable < 0) return Promise.reject({ code: -32000, message: '请求队列已满，请稍后重试。', data: { kind: 'REQUEST_QUEUE_FULL' } });
        pending.splice(removable, 1)[0].reject({ code: -32000, message: '此请求尚未发送，已让位给控制操作；请重试。', data: { kind: 'REQUEST_QUEUE_PREEMPTED' } });
      }
      return new Promise<T>((resolve, reject) => {
        pending.push({ priority, reject, execute: async () => { try { resolve(await request()); } catch (cause) { reject(cause); } } });
        pump();
      });
    },
  };
}
