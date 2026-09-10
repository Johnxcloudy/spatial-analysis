import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { cp, mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(root, 'apps/desktop/package.json'));
const { chromium, expect } = require('@playwright/test');
const arg = (name, fallback) => { const index = process.argv.indexOf(name); return index < 0 ? fallback : process.argv[index + 1]; };
const realReportPath = arg('--real-report');
const largeReportPath = arg('--large-report');
const executable = arg('--executable');
if (!realReportPath || !largeReportPath || !executable) throw new Error('--real-report, --large-report and --executable are required.');
const real = JSON.parse(await readFile(realReportPath, 'utf8'));
const largeReport = JSON.parse(await readFile(largeReportPath, 'utf8'));
if (!real.ok || !largeReport.ok) throw new Error('Both source fixture reports must pass.');
const large = largeReport.levels.find((item) => item.ok && item.features === 500000 && item.options?.operation === 'clip');
if (!large || !real.projectPath || !real.datasetId) throw new Error('Expected successful 127-feature real and 500000-feature synthetic fixtures.');
const output = path.join(root, '.artifacts', `phase3a-ui-${Date.now()}`);
await mkdir(output);
const copies = {};
for (const [name, projectPath] of [['real', real.projectPath], ['large', large.projectPath]]) {
  const destination = path.join(output, name);
  await cp(path.dirname(projectPath), destination, { recursive: true, errorOnExist: true, force: false });
  copies[name] = path.join(destination, path.basename(projectPath));
}
const sha256 = async (file) => createHash('sha256').update(await readFile(file)).digest('hex');
const report = { ok: false, startedAt: new Date().toISOString(), output, copies, fixtures: { real: path.resolve(realReportPath), large: path.resolve(largeReportPath) }, release: { path: path.resolve(executable), sha256: await sha256(executable) }, thresholds: { domAckP95Ms: 150, eventLoopMaxStallMs: 500 }, hardware: { cpu: os.cpus()[0]?.model, logicalCpus: os.cpus().length, totalMemoryBytes: os.totalmem(), freeMemoryAtStartBytes: os.freemem(), platform: os.platform(), release: os.release() }, checks: [], screenshots: [], pageErrors: [], rpcMeasurements: [], privacy: 'Local-only screenshots may contain real data; report omits row values and geometry. Real source is never opened for mutation.', rpcMeasurementScope: 'Harness RPC helper elapsed time, including native invokes and any query_unready readiness waits/retries; excludes application JS queue waiting. Individual retries are not separately timed. DOM/timer metrics come only from the uninjected real-IPC synthetic pagination phase.' };
let browser;
let page;
const button = (name) => page.getByRole('button', { name, exact: true });
const tab = (name) => page.getByRole('tab', { name, exact: true });
const pagination = () => page.locator('#map-panel .pagination');
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const p95 = (values) => [...values].sort((a, b) => a - b)[Math.ceil(values.length * .95) - 1];

async function rpc(method, params = {}) {
  const start = performance.now();
  let failure = null;
  try {
    return await page.evaluate(async ({ method, params }) => {
      const deadline = Date.now() + 15000;
      for (;;) {
        try { return await window.__TAURI_INTERNALS__.invoke('engine_request', { method, params }); }
        catch (cause) {
          let normalized = cause;
          if (typeof cause === 'string') { try { normalized = JSON.parse(cause); } catch {} }
          if (normalized?.data?.kind !== 'query_unready' || Date.now() >= deadline) throw cause;
          await new Promise((resolve) => setTimeout(resolve, 250));
        }
      }
    }, { method, params });
  } catch (cause) { failure = String(cause); throw cause; }
  finally { report.rpcMeasurements.push({ method, durationMs: performance.now() - start, error: failure, offset: params.offset, limit: params.limit }); }
}

async function adapter(projectPath) {
  await page.evaluate((projectPath) => {
    window.__paginationPickerPath = projectPath;
    if (window.__paginationRestore) return;
    const injectedError = { code: -32000, message: 'Synthetic acceptance query timeout', data: { kind: 'query_timeout' } };
    const intercept = (cmd, payload) => {
      if (cmd === 'plugin:dialog|open' && payload?.options?.title === '打开项目') return { value: window.__paginationPickerPath, error: false };
      const injection = window.__paginationInjection;
      if (injection && cmd === 'engine_request' && payload?.method === 'vector.page' && payload.params?.datasetId === injection.datasetId) {
        injection.calls.push({ params: payload.params, injected: injection.remaining > 0 });
        if (injection.remaining > 0) { injection.remaining--; return { value: injectedError, error: true }; }
      }
      return null;
    };
    const fetch = window.fetch;
    window.fetch = function(input, options) {
      try {
        const url = new URL(typeof input === 'string' ? input : input.url);
        if (url.hostname === 'ipc.localhost') {
          const result = intercept(decodeURIComponent(url.pathname.slice(1)), JSON.parse(options?.body));
          if (result) return Promise.resolve(new Response(JSON.stringify(result.value), { headers: { 'Content-Type': 'application/json', 'Tauri-Response': result.error ? 'error' : 'ok' } }));
        }
      } catch {}
      return fetch.call(this, input, options);
    };
    const webview = window.chrome?.webview;
    const post = webview?.postMessage;
    if (webview && post) webview.postMessage = function(message) {
      try {
        const data = typeof message === 'string' ? JSON.parse(message) : message;
        const result = intercept(data.cmd, data.payload);
        if (result) { queueMicrotask(() => window.__TAURI_INTERNALS__.runCallback(result.error ? data.error : data.callback, result.value)); return; }
      } catch {}
      return post.call(this, message);
    };
    window.__paginationRestore = () => { window.fetch = fetch; if (webview && post) webview.postMessage = post; delete window.__paginationInjection; delete window.__paginationRestore; };
  }, projectPath);
}

async function open(projectPath) {
  await adapter(projectPath);
  await button('打开项目').click();
  await expect(button('刷新工作区')).toBeEnabled();
  await expect(page.locator('.header-project')).not.toContainText('未打开项目');
  const workspace = await rpc('workspace.get', { path: projectPath });
  expect(workspace.tasks.some((item) => item.status === 'running')).toBe(false);
  await tab('地图工作区').click();
  return workspace;
}
async function close() {
  if (await button('保存项目').isEnabled()) await button('保存项目').click();
  await button('关闭项目').click();
  await expect(page.locator('.header-project')).toContainText('未打开项目');
}
async function selectDataset(workspace, id) {
  const index = [...workspace.layers].sort((a, b) => a.order - b.order).findIndex((item) => item.datasetId === id);
  expect(index).toBeGreaterThanOrEqual(0);
  await page.locator('.layer-select').nth(index).click();
}
async function screenshot(name) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  const session = await page.context().newCDPSession(page);
  try {
    const size = await page.evaluate(() => ({ width: innerWidth, height: Math.max(innerHeight, document.documentElement.scrollHeight) }));
    const shot = await session.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true, clip: { x: 0, y: 0, ...size, scale: 1 } });
    await writeFile(path.join(output, `${name}.png`), Buffer.from(shot.data, 'base64'));
  } finally { await session.detach(); }
  report.screenshots.push({ name: `${name}.png`, documentOverflow: overflow });
}
async function exactPage(projectPath, datasetId, offset, limit, total) {
  const params = { path: projectPath, datasetId, offset, limit, sortField: null, descending: false, filter: null };
  const result = await rpc('vector.page', params);
  expect(result.total).toBe(total);
  expect(result.offset).toBe(offset);
  expect(result.rows.length).toBe(Math.min(limit, total - offset));
  await expect(pagination()).toContainText(`${offset + 1}–${offset + result.rows.length} / ${total}`);
  await expect(page.locator('#map-panel .attribute-scroll tbody tr')).toHaveCount(result.rows.length);
  return result;
}
async function probeStart() {
  await page.evaluate(() => {
    const probe = { tabs: [], stalls: [] };
    let expected = performance.now() + 25;
    probe.timer = setInterval(() => { const now = performance.now(); probe.stalls.push(Math.max(0, now - expected)); expected = now + 25; }, 25);
    probe.click = (event) => {
      const target = event.target.closest('[role="tab"]');
      if (!target) return;
      const selectedBefore = target.getAttribute('aria-selected') === 'true';
      const start = performance.now();
      requestAnimationFrame(() => {
        const panel = document.getElementById(target.getAttribute('aria-controls'));
        const rect = panel?.getBoundingClientRect();
        probe.tabs.push({ trusted: event.isTrusted, selectedBefore, selectedAfter: target.getAttribute('aria-selected') === 'true', panelVisible: !!panel && !panel.hidden && rect.width > 0 && rect.height > 0, durationMs: performance.now() - start });
      });
    };
    document.addEventListener('click', probe.click, true);
    window.__paginationProbe = probe;
  });
}

try {
  browser = await chromium.connectOverCDP(arg('--cdp', 'http://127.0.0.1:9224'));
  for (const candidate of browser.contexts()[0].pages()) {
    if (await candidate.evaluate(() => !!window.__TAURI_INTERNALS__?.invoke && document.querySelector('.brand')?.textContent.includes('Spatial Analysis')).catch(() => false)) { page = candidate; break; }
  }
  if (!page) throw new Error('Native WebView not found.');
  page.setDefaultTimeout(30000);
  page.on('pageerror', (error) => report.pageErrors.push(String(error)));
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(page.locator('.dirty-dot')).toHaveCount(0);
  await expect(button('取消当前任务')).toHaveCount(0);
  report.runtime = await rpc('runtime.info');
  expect(report.runtime.packaged).toBe(true);
  expect(report.runtime.engineVersion).toBe('0.6.1');
  expect(report.runtime.protocolVersion).toBe(6);
  await expect(page.locator('.version-label')).toHaveText('0.6.1');

  const realWorkspace = await open(copies.real);
  const realDataset = realWorkspace.datasets.find((item) => item.id === real.datasetId);
  expect(realDataset.featureCount).toBe(127);
  expect(realDataset.report.counts.invalid).toBe(1);
  await selectDataset(realWorkspace, real.datasetId);
  await page.getByLabel('每页记录数', { exact: true }).selectOption('50');
  const first = await exactPage(copies.real, real.datasetId, 0, 50, 127);
  await button('定位当前图层').click();
  await expect(page.locator('.map-target canvas').first()).toBeVisible();
  await button('属性末页').click();
  const last = await exactPage(copies.real, real.datasetId, 100, 50, 127);
  await button('数据与检查报告').click();
  const invalid = realDataset.report.checks.find((item) => item.count === 1 && !item.passed);
  expect(invalid).toBeTruthy();
  const invalidCheck = page.locator('.import-checks').getByText(invalid.detail, { exact: false });
  await expect(invalidCheck).toBeVisible();
  await expect(invalidCheck).toContainText('数量：1');
  await screenshot('real-127-last-page-quality');
  await invalidCheck.scrollIntoViewIfNeeded();
  await screenshot('real-127-quality-detail');
  await close();
  await open(copies.real);
  await page.getByLabel('每页记录数', { exact: true }).selectOption('50');
  expect(await exactPage(copies.real, real.datasetId, 0, 50, 127)).toEqual(first);
  await button('属性末页').click();
  expect(await exactPage(copies.real, real.datasetId, 100, 50, 127)).toEqual(last);
  await screenshot('real-127-reopened');
  report.checks.push({ name: 'real127-first50-last27-quality1-invalid-reopen', ok: true });
  await close();

  const largeWorkspace = await open(copies.large);
  await selectDataset(largeWorkspace, large.inputDatasetId);
  await exactPage(copies.large, large.inputDatasetId, 0, 200, 500000);
  await button('属性末页').click();
  await exactPage(copies.large, large.inputDatasetId, 499800, 200, 500000);
  await screenshot('synthetic500000-last-page');
  await probeStart();
  for (let index = 0; index < 16; index++) {
    await page.getByLabel('每页记录数', { exact: true }).selectOption(index % 2 ? '200' : '50');
    await tab('用地分析').click();
    await expect(tab('用地分析')).toHaveAttribute('aria-selected', 'true');
    await tab('地图工作区').click();
    await expect(tab('地图工作区')).toHaveAttribute('aria-selected', 'true');
    const limit = index % 2 ? 200 : 50;
    await exactPage(copies.large, large.inputDatasetId, 0, limit, 500000);
    await button('属性末页').click();
    await exactPage(copies.large, large.inputDatasetId, 500000 - limit, limit, 500000);
  }
  report.metrics = await page.evaluate(() => { const probe = window.__paginationProbe; clearInterval(probe.timer); document.removeEventListener('click', probe.click, true); return { tabs: probe.tabs, stalls: probe.stalls }; });
  expect(report.metrics.tabs.length).toBeGreaterThanOrEqual(32);
  expect(report.metrics.tabs.every((item) => item.trusted && !item.selectedBefore && item.selectedAfter && item.panelVisible)).toBe(true);
  expect(report.metrics.stalls.length).toBeGreaterThanOrEqual(20);
  report.measurements = { domAckP95Ms: p95(report.metrics.tabs.map((item) => item.durationMs)), eventLoopMaxStallMs: Math.max(...report.metrics.stalls), tabSamples: report.metrics.tabs.length, timerSamples: report.metrics.stalls.length };
  expect(report.measurements.domAckP95Ms).toBeLessThanOrEqual(150);
  expect(report.measurements.eventLoopMaxStallMs).toBeLessThanOrEqual(500);
  report.checks.push({ name: 'uninjected500000-pagination-hidden-switch-and-interaction', ok: true, iterations: 16 });

  if (process.argv.includes('--inject-timeout')) {
    await page.evaluate((datasetId) => { window.__paginationInjection = { datasetId, remaining: 1, calls: [] }; }, large.inputDatasetId);
    await page.getByLabel('每页记录数', { exact: true }).selectOption('50');
    await expect(button('重试当前页')).toBeVisible();
    await expect(page.locator('.page-query-failure')).toContainText('Synthetic acceptance query timeout');
    await expect(page.locator('.error-banner')).toHaveCount(0);
    await expect(page.getByText('暂无属性记录', { exact: true })).toHaveCount(0);
    await screenshot('injected-timeout-explicit-retry');
    await pause(400);
    expect(await page.evaluate(() => window.__paginationInjection.calls.length)).toBe(1);
    await button('重试当前页').click();
    await expect(pagination()).toContainText('1–50 / 500000');
    await expect(page.locator('.page-query-failure')).toHaveCount(0);
    await expect(page.locator('.error-banner')).toHaveCount(0);
    const injected = await page.evaluate(() => { const result = window.__paginationInjection; delete window.__paginationInjection; return result; });
    expect(injected.calls.length).toBe(2);
    expect(injected.calls[0].injected).toBe(true);
    expect(injected.calls[1].injected).toBe(false);
    expect(injected.calls[1].params).toEqual(injected.calls[0].params);
    report.injection = { ...injected, scope: 'One deliberately fabricated query_timeout; second call forwarded unchanged to real native backend. Not cold IO or performance evidence. Local error clears; no global error is created or cleared.' };
    await screenshot('injected-timeout-real-retry-success');
    report.checks.push({ name: 'explicit-synthetic-fault-same-params-real-retry-no-loop', ok: true });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await screenshot('synthetic500000-narrow');
  await page.setViewportSize({ width: 1440, height: 900 });
  await close();
  expect(report.pageErrors).toHaveLength(0);
  expect(await sha256(executable)).toBe(report.release.sha256);
  report.ok = true;
} catch (cause) {
  report.error = String(cause.stack || cause);
  if (page) { try { await screenshot('failure'); } catch {} }
  process.exitCode = 1;
} finally {
  if (page) { try { await page.evaluate(() => { const probe = window.__paginationProbe; if (probe) { clearInterval(probe.timer); document.removeEventListener('click', probe.click, true); } window.__paginationRestore?.(); }); } catch {} }
  report.finishedAt = new Date().toISOString();
  await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ok: report.ok, output, measurements: report.measurements, error: report.error }, null, 2));
  await browser?.close();
}
