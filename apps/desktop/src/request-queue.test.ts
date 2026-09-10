import { describe, expect, it } from 'vitest';
import { createRequestQueue } from './request-queue';

describe('bounded priority RPC queue', () => {
  it('sends control before pending display and never overlaps requests', async () => {
    const queue = createRequestQueue(4);
    const order: string[] = [];
    let release!: () => void;
    const first = queue.run('display', async () => { order.push('first'); await new Promise<void>((resolve) => { release = resolve; }); });
    const display = queue.run('display', async () => { order.push('display'); });
    const cancel = queue.run('control', async () => { order.push('cancel'); });
    expect(order).toEqual(['first']);
    release();
    await Promise.all([first, display, cancel]);
    expect(order).toEqual(['first', 'cancel', 'display']);
  });
  it('rejects overflow without executing or displacing accepted controls', async () => {
    const queue = createRequestQueue(1);
    let release!: () => void;
    const first = queue.run('display', () => new Promise<void>((resolve) => { release = resolve; }));
    const next = queue.run('control', async () => 'accepted');
    await expect(queue.run('display', async () => 'overflow')).rejects.toMatchObject({ data: { kind: 'REQUEST_QUEUE_FULL' } });
    release();
    await first;
    expect(await next).toBe('accepted');
  });

  it('admits cancellation ahead of saturated display and ordinary controls', async () => {
    const queue = createRequestQueue(2);
    let release!: () => void;
    const order: string[] = [];
    const active = queue.run('display', () => new Promise<void>((resolve) => { release = resolve; }));
    const evicted = queue.run('display', async () => order.push('obsolete'));
    const rejected = expect(evicted).rejects.toMatchObject({ data: { kind: 'REQUEST_QUEUE_PREEMPTED' } });
    const control = queue.run('control', async () => order.push('status'));
    const cancel = queue.run('urgent', async () => order.push('cancel'));
    release();
    await Promise.all([active, rejected, control, cancel]);
    expect(order).toEqual(['cancel', 'status']);
  });

  it('keeps cancel executable even when all waiting slots hold control requests', async () => {
    const queue = createRequestQueue(1);
    let release!: () => void;
    const active = queue.run('display', () => new Promise<void>((resolve) => { release = resolve; }));
    const delayed = queue.run('control', async () => 'not sent');
    const rejected = expect(delayed).rejects.toMatchObject({ data: { kind: 'REQUEST_QUEUE_PREEMPTED' } });
    const cancel = queue.run('urgent', async () => 'cancelled');
    release();
    await Promise.all([active, rejected]);
    expect(await cancel).toBe('cancelled');
  });
});
