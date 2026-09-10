import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { cp, mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { verifyCartographyUi } from './cartography-ui-checks.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(root, 'apps/desktop/package.json'));
const { chromium, expect } = require('@playwright/test');
const arg = (name, fallback) => { const index = process.argv.indexOf(name); return index < 0 ? fallback : process.argv[index + 1]; };
const realReportPath = arg('--real-report');
const largeReportPath = arg('--large-report');
const executable = arg('--executable');
const expectedVersion = arg('--expected-version', '0.7.0');
const diagnostics = process.argv.includes('--diagnostics');
const cartography = process.argv.includes('--cartography');
if (cartography && !diagnostics) throw new Error('--cartography requires --diagnostics for geometry-refetch assertions.');
if (!realReportPath || !largeReportPath || !executable) throw new Error('--real-report, --large-report and --executable are required.');
const real = JSON.parse(await readFile(realReportPath, 'utf8'));
const largeReport = JSON.parse(await readFile(largeReportPath, 'utf8'));
if (!real.ok || !largeReport.ok) throw new Error('Both source fixture reports must pass.');
const large = largeReport.levels.find((item) => item.ok && item.features === 500000 && item.options?.operation === 'clip');
if (!large || !real.projectPath || !real.datasetId) throw new Error('Expected successful 127-feature real and 500000-feature synthetic fixtures.');
const output = path.join(root, '.artifacts', `${cartography ? 'phase4a1-ui' : diagnostics ? 'phase3b-ui-investigation' : 'phase3a-ui'}-${Date.now()}`);
await mkdir(output);
const copies = {};
for (const [name, projectPath] of [['real', real.projectPath], ['large', large.projectPath]]) {
  const destination = path.join(output, name);
  await cp(path.dirname(projectPath), destination, { recursive: true, errorOnExist: true, force: false });
  copies[name] = path.join(destination, path.basename(projectPath));
}
const cartographyCases = [];
if (cartography) for (const features of [100000, 250000, 500000]) {
  const fixture = largeReport.levels.find((item) => item.ok && item.features === features && item.options?.operation === 'clip');
  if (!fixture) throw new Error(`Missing successful ${features} cartography fixture.`);
  if (features !== 500000) {
    const destination = path.join(output, `large${features}`);
    await cp(path.dirname(fixture.projectPath), destination, { recursive: true, errorOnExist: true, force: false });
    copies[`large${features}`] = path.join(destination, path.basename(fixture.projectPath));
  }
  cartographyCases.push({ features, inputDatasetId: fixture.inputDatasetId, projectPath: features === 500000 ? copies.large : copies[`large${features}`] });
}
const sha256 = async (file) => createHash('sha256').update(await readFile(file)).digest('hex');
const report = { ok: false, startedAt: new Date().toISOString(), output, copies, fixtures: { real: path.resolve(realReportPath), large: path.resolve(largeReportPath) }, release: { path: path.resolve(executable), sha256: await sha256(executable) }, thresholds: { domAckP95Ms: 150, eventLoopMaxStallMs: 500 }, hardware: { cpu: os.cpus()[0]?.model, logicalCpus: os.cpus().length, totalMemoryBytes: os.totalmem(), freeMemoryAtStartBytes: os.freemem(), platform: os.platform(), release: os.release() }, checks: [], screenshots: [], pageErrors: [], rpcMeasurements: [], privacy: 'Local-only screenshots may contain real data; report omits row values and geometry. Real source is never opened for mutation.', rpcMeasurementScope: 'Harness RPC helper elapsed time, including native invokes and any query_unready readiness waits/retries; excludes application JS queue waiting. Every helper attempt and readiness wait is separately timed in the browser; fetch completion is response receipt, WebView fallback completion is unavailable. Diagnostics add observation overhead. DOM/timer metrics come only from the uninjected real-IPC synthetic pagination phase.' };
let browser;
let page;
let rpcSequence = 0;
const button = (name) => page.getByRole('button', { name, exact: true });
const tab = (name) => page.getByRole('tab', { name, exact: true });
const pagination = () => page.locator('#map-panel .pagination');
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const p95 = (values) => [...values].sort((a, b) => a - b)[Math.ceil(values.length * .95) - 1];

async function rpc(method, params = {}) {
  const start = performance.now();
  const rpcId = `helper-${++rpcSequence}`;
  let failure = null;
  let trace = null;
  try {
    const envelope = await page.evaluate(async ({ method, params, rpcId, diagnostics }) => {
      const deadline = Date.now() + 15000;
      const trace = { rpcId, phase: window.__paginationTrace?.phase ?? 'unobserved', startEpochMs: performance.timeOrigin + performance.now(), attempts: [], readinessWaits: [] };
      const normalizedError = (cause) => {
        let normalized = cause;
        if (typeof cause === 'string') { try { normalized = JSON.parse(cause); } catch {} }
        return { kind: normalized?.data?.kind ?? null, code: typeof normalized?.code === 'number' ? normalized.code : null };
      };
      for (;;) {
        const attempt = { attemptId: `${rpcId}-${trace.attempts.length + 1}`, observerActive: diagnostics && window.__paginationObserverActive === true, startEpochMs: performance.timeOrigin + performance.now(), errorKind: null, errorCode: null };
        const began = performance.now();
        trace.attempts.push(attempt);
        let result;
        let failed = false;
        try { result = await window.__TAURI_INTERNALS__.invoke('engine_request', { method, params }, diagnostics ? { headers: { 'x-spatial-probe-attempt': attempt.attemptId } } : undefined); }
        catch (cause) {
          failed = true;
          const normalized = normalizedError(cause);
          attempt.errorKind = normalized.kind;
          attempt.errorCode = normalized.code;
        }
        attempt.endEpochMs = performance.timeOrigin + performance.now();
        attempt.durationMs = performance.now() - began;
        attempt.ok = !failed;
        if (!failed) { trace.endEpochMs = performance.timeOrigin + performance.now(); return { ok: true, result, trace }; }
        if (attempt.errorKind !== 'query_unready' || Date.now() >= deadline) { trace.endEpochMs = performance.timeOrigin + performance.now(); return { ok: false, errorKind: attempt.errorKind, errorCode: attempt.errorCode, trace }; }
        const wait = { afterAttemptId: attempt.attemptId, requestedMs: 250, startEpochMs: performance.timeOrigin + performance.now() };
        const waiting = performance.now();
        await new Promise((resolve) => setTimeout(resolve, 250));
        wait.endEpochMs = performance.timeOrigin + performance.now();
        wait.durationMs = performance.now() - waiting;
        trace.readinessWaits.push(wait);
      }
    }, { method, params, rpcId, diagnostics });
    trace = envelope.trace;
    if (!envelope.ok) throw new Error(`Native helper failed: ${envelope.errorKind ?? 'unknown'} (${envelope.errorCode ?? 'unknown'})`);
    return envelope.result;
  } catch (cause) { failure = String(cause); throw cause; }
  finally {
    const durationMs = performance.now() - start;
    const nativeAttemptsMs = trace?.attempts.reduce((sum, item) => sum + item.durationMs, 0) ?? null;
    const readinessWaitMs = trace?.readinessWaits.reduce((sum, item) => sum + item.durationMs, 0) ?? null;
    report.rpcMeasurements.push({ rpcId, method, durationMs, error: failure, offset: params.offset, limit: params.limit, nativeAttemptsMs, readinessWaitMs, otherHelperMs: trace ? durationMs - nativeAttemptsMs - readinessWaitMs : null, trace });
  }
}

async function adapter(projectPath) {
  await page.evaluate(({ projectPath, diagnostics }) => {
    window.__paginationPickerPath = projectPath;
    if (window.__paginationRestore) return;
    const trace = { events: [], actions: [], droppedEvents: 0, phase: 'initialization', sequence: 0, aliases: new Map(), maxRecords: 10000 };
    window.__paginationTrace = trace;
    window.__paginationObserverActive = diagnostics;
    const action = (event) => {
      if (!diagnostics || !event.isTrusted) return;
      const target = event.target.closest('button,[role="tab"],select');
      if (!target) return;
      if (trace.actions.length >= trace.maxRecords) { trace.droppedEvents++; return; }
      const label = target.getAttribute('aria-label');
      trace.actions.push({ actionId: trace.actions.length + 1, phase: trace.phase, epochMs: performance.timeOrigin + performance.now(), type: event.type, role: target.getAttribute('role'), label, isLayerSelection: target.classList.contains('layer-select'), pageSize: label === '每页记录数' ? Number(target.value) : null });
    };
    document.addEventListener('click', action, true);
    document.addEventListener('change', action, true);
    const alias = (value) => { if (value == null) return null; if (!trace.aliases.has(value)) trace.aliases.set(value, `resource-${trace.aliases.size + 1}`); return trace.aliases.get(value); };
    const observe = (cmd, payload, headers, transport) => {
      if (!diagnostics || cmd !== 'engine_request') return null;
      if (trace.events.length >= trace.maxRecords) { trace.droppedEvents++; return null; }
      let marker = null;
      try { marker = new Headers(headers).get('x-spatial-probe-attempt'); } catch {}
      const params = payload?.params ?? {};
      const record = { sequence: ++trace.sequence, transport, helperAttemptId: marker, origin: marker ? 'harness-marker' : 'unattributed', method: String(payload?.method ?? '').slice(0, 80), projectAlias: alias(params.path), datasetAlias: alias(params.datasetId), offset: params.offset, limit: params.limit, filtered: !!params.filter, sorted: !!params.sortField, phase: trace.phase, startEpochMs: performance.timeOrigin + performance.now(), completionAvailable: false };
      trace.events.push(record);
      return record;
    };
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
      let observed = null;
      try {
        const url = new URL(typeof input === 'string' ? input : input.url);
        if (url.hostname === 'ipc.localhost') {
          const cmd = decodeURIComponent(url.pathname.slice(1));
          const payload = JSON.parse(options?.body);
          observed = observe(cmd, payload, options?.headers, 'fetch');
          const result = intercept(cmd, payload);
          if (observed && result) observed.injected = true;
          if (result) return Promise.resolve(new Response(JSON.stringify(result.value), { headers: { 'Content-Type': 'application/json', 'Tauri-Response': result.error ? 'error' : 'ok' } }));
        }
      } catch {}
      return fetch.call(this, input, options).then((response) => {
        if (observed) {
          observed.endEpochMs = performance.timeOrigin + performance.now();
          observed.durationMs = observed.endEpochMs - observed.startEpochMs;
          observed.completionAvailable = true;
          observed.responseHeader = response.headers.get('Tauri-Response');
          if (observed.responseHeader === 'error') response.clone().json().then((cause) => { if (typeof cause === 'string') { try { cause = JSON.parse(cause); } catch {} } observed.errorKind = cause?.data?.kind ?? null; }).catch(() => { observed.errorKind = 'unreadable_transport_error'; });
        }
        return response;
      }, (cause) => { if (observed) { observed.endEpochMs = performance.timeOrigin + performance.now(); observed.durationMs = observed.endEpochMs - observed.startEpochMs; observed.completionAvailable = true; observed.errorKind = 'transport_rejection'; } throw cause; });
    };
    const webview = window.chrome?.webview;
    const post = webview?.postMessage;
    if (webview && post) webview.postMessage = function(message) {
      try {
        const data = typeof message === 'string' ? JSON.parse(message) : message;
        const observed = observe(data.cmd, data.payload, data.options?.headers, 'webview');
        const result = intercept(data.cmd, data.payload);
        if (observed && result) observed.injected = true;
        if (result) { queueMicrotask(() => window.__TAURI_INTERNALS__.runCallback(result.error ? data.error : data.callback, result.value)); return; }
      } catch {}
      return post.call(this, message);
    };
    window.__paginationRestore = () => { window.fetch = fetch; if (webview && post) webview.postMessage = post; document.removeEventListener('click', action, true); document.removeEventListener('change', action, true); window.__paginationObserverActive = false; delete window.__paginationInjection; delete window.__paginationRestore; };
  }, { projectPath, diagnostics });
}
async function phase(name) {
  await page.evaluate((name) => { if (window.__paginationTrace) window.__paginationTrace.phase = name; }, name);
}
function classifyObservedAttempts(measurements, observation) {
  const attempts = measurements.flatMap((item) => item.trace?.attempts ?? []);
  const eligible = attempts.filter((item) => item.observerActive === true);
  observation.notObservedBeforeAdapter = attempts.filter((item) => item.observerActive !== true).map((item) => ({ attemptId: item.attemptId, startEpochMs: item.startEpochMs, endEpochMs: item.endEpochMs, durationMs: item.durationMs, reason: 'observer was not active at attempt start; retained but not attributable' }));
  observation.markerCoverageDenominator = eligible.length;
  const observed = new Set(observation.events.map((item) => item.helperAttemptId).filter(Boolean));
  const missing = eligible.filter((item) => !observed.has(item.attemptId)).map((item) => item.attemptId);
  observation.markerAttributionVerified = eligible.length > 0 && missing.length === 0 && observation.droppedEvents === 0;
  observation.missingHelperAttemptMarkers = missing;
  if (observation.markerAttributionVerified) observation.events.forEach((item) => { if (item.origin === 'unattributed') item.origin = 'application'; });
  observation.correlationBoundary = 'Header attribution covers only attempts started with the observer active; earlier attempts remain unobserved. Action/host-log time overlap is temporal association, not proven causality or one-to-one host request mapping. WebView fallback records dispatch only. No application JS queue entry times are observed.';
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
  await adapter(null);
  report.runtime = await rpc('runtime.info');
  expect(report.runtime.packaged).toBe(true);
  expect(report.runtime.engineVersion).toBe(expectedVersion);
  expect(report.runtime.protocolVersion).toBe(7);
  await expect(page.locator('.version-label')).toHaveText(expectedVersion);

  await phase('real127');
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

  await phase('synthetic500000-uninjected');
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

  if (cartography) await verifyCartographyUi({ page, expect, rpc, open, close, selectDataset, screenshot, phase, cases: cartographyCases, report });
  if (process.argv.includes('--inject-timeout')) {
    await phase('explicitly-injected-timeout');
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
  if (page && diagnostics) {
    try {
      report.diagnostics = await page.evaluate(() => { const trace = window.__paginationTrace; return trace ? { events: trace.events, actions: trace.actions, droppedEvents: trace.droppedEvents, endEpochMs: performance.timeOrigin + performance.now() } : null; });
      if (report.diagnostics) {
        classifyObservedAttempts(report.rpcMeasurements, report.diagnostics);
      }
    } catch (cause) { report.diagnosticsError = String(cause); }
  }
  if (page) { try { await page.evaluate(() => { const probe = window.__paginationProbe; if (probe) { clearInterval(probe.timer); document.removeEventListener('click', probe.click, true); } window.__paginationRestore?.(); }); } catch {} }
  report.finishedAt = new Date().toISOString();
  await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ok: report.ok, output, measurements: report.measurements, error: report.error }, null, 2));
  await browser?.close();
}
