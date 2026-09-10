import { createRequire } from 'node:module';
import { cp, mkdir, readFile, stat, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(root, 'apps/desktop/package.json'));
const { chromium, expect } = require('@playwright/test');
const arg = (name, fallback) => { const index = process.argv.indexOf(name); return index < 0 ? fallback : process.argv[index + 1]; };
const fixturePath = arg('--fixtures');
const packaged = process.argv.includes('--packaged');
const completeLarge = process.argv.includes('--complete-large');
if (!fixturePath) throw new Error('--fixtures must name a successful verify-analysis.py report.json');
const fixture = JSON.parse(await readFile(fixturePath, 'utf8'));
if (!fixture.ok) throw new Error('Source analysis verification must pass first.');
const cases = fixture.levels.filter((item) => item.ok && item.projectPath && item.options?.operation === 'clip' && Number.isFinite(item.features)).sort((a, b) => a.features - b.features);
if (!cases.length) throw new Error('No successful clip fixture with projectPath/options/features.');
const largest = cases.at(-1);
const smallest = cases[0];
if (largest.features > 10000 && smallest.features > 10000) throw new Error('Include a <=10000 feature clip level for recovery acceptance.');
const output = path.join(root, '.artifacts', `analysis-ui-${Date.now()}`);
await mkdir(output);
const copies = {};
for (const [name, item] of [['stress', largest], ['recovery', smallest]]) {
  const destination = path.join(output, name);
  await cp(path.dirname(item.projectPath), destination, { recursive: true, errorOnExist: true, force: false });
  copies[name] = path.join(destination, path.basename(item.projectPath));
}
const thresholds = { domAckP95Ms: 150, eventLoopMaxStallMs: 500, taskStatusP95Ms: 1000, cancelTerminalMs: 5000 };
const report = { ok: false, startedAt: new Date().toISOString(), fixturePath: path.resolve(fixturePath), copies, thresholds, stressFeatures: largest.features, recoveryFeatures: smallest.features, hardware: { platform: os.platform(), release: os.release(), architecture: os.arch(), cpu: os.cpus()[0]?.model, logicalCpus: os.cpus().length, totalMemoryBytes: os.totalmem(), freeMemoryAtStartBytes: os.freemem(), node: process.version, workspace: root }, checks: [], screenshots: [], pageErrors: [] };
report.packaged = packaged;
report.completeLarge = completeLarge;
report.rpcMeasurementScope = packaged ? 'Harness-issued real native invoke, including Rust/backend wait; excludes application JavaScript queue wait. UI RPC requests remain real but are not counted by this probe.' : 'Application desktop.request promises, including JavaScript queue wait and native/backend wait.';
let browser;
let page;
const button = (name) => page.getByRole('button', { name, exact: true });
const tab = (name) => page.getByRole('tab', { name, exact: true });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const p95 = (values) => values.length ? [...values].sort((a, b) => a - b)[Math.ceil(values.length * 0.95) - 1] : null;

async function bridgeCall(method, params) {
  return page.evaluate(async ({ method, params, packaged }) => {
    if (packaged) {
      const probe = window.__analysisProbe;
      const sampled = probe?.phase;
      const started = performance.now();
      if (sampled) { probe.activeRequests++; probe.peakOutstandingRequests = Math.max(probe.peakOutstandingRequests, probe.activeRequests); }
      let error = null;
      try {
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
      } catch (cause) { error = cause?.data?.kind || String(cause); throw cause; }
      finally { if (sampled) { probe.activeRequests--; probe.requests.push({ method, durationMs: performance.now() - started, error }); } }
    }
    const address = performance.getEntriesByType('resource').map((entry) => entry.name).find((name) => new URL(name).pathname === '/src/bridge.ts') ?? '/src/bridge.ts';
    const { desktop } = await import(address);
    return desktop.request(method, params);
  }, { method, params, packaged });
}
async function pickers(projectPath) {
  await page.evaluate(async ({ projectPath, output, packaged }) => {
    if (packaged) {
      window.__analysisPickerPaths = { projectPath, output };
      if (window.__analysisPickerRestore) return;
      const chosen = (cmd, payload) => {
        if (cmd === 'plugin:dialog|open' && payload?.options?.title === '打开项目') return window.__analysisPickerPaths.projectPath;
        if (cmd === 'plugin:dialog|save' && payload?.options?.title === '导出 GeoPackage') return `${window.__analysisPickerPaths.output}/result-export.gpkg`;
        if (cmd === 'plugin:dialog|save' && payload?.options?.title === '导出分类统计 CSV') return `${window.__analysisPickerPaths.output}/statistics-export.csv`;
        return undefined;
      };
      const originalFetch = window.fetch;
      window.fetch = function(input, options) {
        try {
          const url = new URL(typeof input === 'string' ? input : input.url);
          if (url.hostname === 'ipc.localhost') {
            const result = chosen(decodeURIComponent(url.pathname.slice(1)), JSON.parse(options?.body));
            if (result !== undefined) return Promise.resolve(new Response(JSON.stringify(result), { headers: { 'Content-Type': 'application/json', 'Tauri-Response': 'ok' } }));
          }
        } catch {}
        return originalFetch.call(this, input, options);
      };
      const webview = window.chrome?.webview;
      const originalPost = webview?.postMessage;
      if (webview && originalPost) webview.postMessage = function(message) {
        try {
          const data = typeof message === 'string' ? JSON.parse(message) : message;
          const result = chosen(data.cmd, data.payload);
          if (result !== undefined) { queueMicrotask(() => window.__TAURI_INTERNALS__.runCallback(data.callback, result)); return; }
        } catch {}
        return originalPost.call(this, message);
      };
      window.__analysisPickerRestore = () => { window.fetch = originalFetch; if (webview && originalPost) webview.postMessage = originalPost; delete window.__analysisPickerRestore; };
      return;
    }
    const addresses = performance.getEntriesByType('resource').map((entry) => entry.name).filter((name) => new URL(name).pathname === '/src/bridge.ts');
    for (const address of new Set(addresses.length ? addresses : ['/src/bridge.ts'])) {
      const { desktop } = await import(address);
      desktop.chooseProject = () => Promise.resolve(projectPath);
      desktop.chooseExport = () => Promise.resolve(`${output}/result-export.gpkg`);
      desktop.chooseCsvExport = () => Promise.resolve(`${output}/statistics-export.csv`);
    }
  }, { projectPath, output, packaged });
}
async function screenshot(name) {
  await page.evaluate(() => document.fonts.ready);
  if (name.includes('statistics')) await page.getByRole('table', { name: '分类面积统计', exact: true }).scrollIntoViewIfNeeded();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  report.checks.push({ name: `${name}-horizontal-overflow`, pixels: overflow, ok: overflow <= 1 });
  const session = await page.context().newCDPSession(page);
  try {
    const size = await page.evaluate(() => ({ width: innerWidth, height: Math.max(innerHeight, document.documentElement.scrollHeight) }));
    const capture = await session.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true, clip: { x: 0, y: 0, ...size, scale: 1 } });
    await writeFile(path.join(output, `${name}.png`), Buffer.from(capture.data, 'base64'));
  } finally { await session.detach(); }
  report.screenshots.push(`${name}.png`);
  expect(overflow).toBeLessThanOrEqual(1);
}
async function openProject(projectPath) {
  await pickers(projectPath);
  await button('打开项目').click();
  await expect.poll(async () => {
    try { return (await bridgeCall('workspace.get', { path: projectPath })).projectId; }
    catch { return ''; }
  }, { timeout: 30000 }).not.toBe('');
  await expect(button('刷新工作区')).toBeEnabled();
}
async function closeProject() {
  if (await button('保存项目').isEnabled()) await button('保存项目').click();
  await expect(button('关闭项目')).toBeEnabled();
  await button('关闭项目').click();
  await expect(page.locator('.header-project')).toContainText('未打开项目');
}
async function configure(item, name) {
  const options = item.options;
  await tab('用地分析').click();
  await page.getByLabel('分析方式', { exact: true }).selectOption(options.operation);
  await page.getByLabel('成果名称', { exact: true }).fill(name);
  await page.getByLabel('用地数据集', { exact: true }).selectOption(options.inputDatasetId);
  await page.getByLabel('叠加数据集', { exact: true }).selectOption(options.overlayDatasetId);
  await page.getByLabel('左侧地类字段', { exact: true }).selectOption(options.inputClassField);
  if (options.operation === 'intersect') await page.getByLabel('右侧地类字段', { exact: true }).selectOption(options.overlayClassField);
  await page.getByLabel('分类标准及版本', { exact: true }).fill(options.classificationStandard);
  await page.getByLabel('分析投影', { exact: true }).fill(options.analysisCrs);
  await page.getByLabel('投影适用理由', { exact: true }).fill(options.crsReason);
}
async function latestTask(projectPath, kind, excluding = '') {
  let found;
  await expect.poll(async () => {
    const workspace = await bridgeCall('workspace.get', { path: projectPath });
    found = workspace.tasks.find((task) => task.kind === kind && task.id !== excluding);
    return found?.id ?? '';
  }, { timeout: 30000 }).not.toBe('');
  return found;
}
async function terminal(projectPath, id, timeoutMs = 900000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const task = await bridgeCall('task.get', { path: projectPath, taskId: id });
    if (task.status !== 'running') return task;
    if (Date.now() >= deadline) throw new Error(`Task ${id} did not reach terminal state within ${timeoutMs} ms.`);
    await sleep(150);
  }
}
async function installProbe() {
  await page.evaluate(async (packaged) => {
    const probe = { phase: false, acknowledgements: [], stalls: [], requests: [], activeRequests: 0, peakOutstandingRequests: 0, restore: [] };
    window.__analysisProbe = probe;
    let expected = performance.now() + 25;
    probe.timer = setInterval(() => {
      const now = performance.now();
      if (probe.phase && probe.stalls.length < 50000) probe.stalls.push(Math.max(0, now - expected));
      expected = now + 25;
    }, 25);
    probe.click = (event) => {
      if (!probe.phase) return;
      const element = event.target.closest('[role="tab"],button');
      if (!element || element.disabled) return;
      const started = performance.now();
      const name = element.getAttribute('aria-label') || element.textContent;
      const isTab = element.getAttribute('role') === 'tab';
      const beforeSelected = isTab ? element.getAttribute('aria-selected') === 'true' : null;
      const panel = isTab ? document.getElementById(element.getAttribute('aria-controls')) : null;
      requestAnimationFrame(() => {
        const rect = panel?.getBoundingClientRect();
        if (probe.acknowledgements.length < 50000) probe.acknowledgements.push({ name, trusted: event.isTrusted, beforeSelected, durationMs: performance.now() - started, tabSelected: isTab ? element.getAttribute('aria-selected') === 'true' : null, panelVisible: isTab ? !panel.hidden && rect.width > 0 && rect.height > 0 : null });
      });
    };
    document.addEventListener('click', probe.click, true);
    if (packaged) return;
    const addresses = performance.getEntriesByType('resource').map((entry) => entry.name).filter((name) => new URL(name).pathname === '/src/bridge.ts');
    for (const address of new Set(addresses.length ? addresses : ['/src/bridge.ts'])) {
      const { desktop } = await import(address);
      const original = desktop.request;
      const observed = async (...args) => {
        const sampled = probe.phase;
        const started = performance.now();
        if (sampled) { probe.activeRequests++; probe.peakOutstandingRequests = Math.max(probe.peakOutstandingRequests, probe.activeRequests); }
        let error = null;
        let responseSummary = null;
        try {
          const response = await original(...args);
          responseSummary = { returnedCount: response?.returnedCount ?? response?.rows?.length ?? null, truncated: response?.truncated ?? null, status: response?.status ?? null };
          return response;
        }
        catch (cause) { error = cause?.data?.kind || String(cause); throw cause; }
        finally {
          if (sampled) { probe.activeRequests--; if (probe.requests.length < 50000) probe.requests.push({ method: args[0], durationMs: performance.now() - started, error, responseSummary }); }
        }
      };
      desktop.request = observed;
      probe.restore.push(() => { if (desktop.request === observed) desktop.request = original; });
    }
  }, packaged);
}

async function collectMetrics() {
  return page.evaluate(() => {
    const probe = window.__analysisProbe;
    probe.phase = false;
    return { acknowledgements: probe.acknowledgements, stalls: probe.stalls, requests: probe.requests, peakOutstandingRequests: probe.peakOutstandingRequests };
  });
}
function summarizeMetrics(metrics) {
  const tabs = metrics.acknowledgements.filter((item) => item.tabSelected !== null);
  const statusTimes = metrics.requests.filter((item) => item.method === 'task.get' && !item.error).map((item) => item.durationMs);
  expect(tabs.length).toBeGreaterThanOrEqual(16);
  expect(tabs.every((item) => item.trusted && item.beforeSelected === false && item.tabSelected && item.panelVisible)).toBe(true);
  expect(metrics.stalls.length).toBeGreaterThanOrEqual(20);
  expect(statusTimes.length).toBeGreaterThanOrEqual(8);
  return { domAckSamples: tabs.length, domAckP95Ms: p95(tabs.map((item) => item.durationMs)), eventLoopSamples: metrics.stalls.length, eventLoopMaxStallMs: Math.max(0, ...metrics.stalls), taskStatusSamples: statusTimes.length, taskStatusP95Ms: p95(statusTimes) };
}

try {
  browser = await chromium.connectOverCDP(arg('--cdp', 'http://127.0.0.1:9224'));
  if (packaged) {
    for (const candidate of browser.contexts()[0].pages()) {
      if (await candidate.evaluate(() => !!window.__TAURI_INTERNALS__?.invoke && document.querySelector('.brand')?.textContent.includes('Spatial Analysis')).catch(() => false)) { page = candidate; break; }
    }
  } else page = browser.contexts()[0].pages().find((candidate) => candidate.url().includes('1420'));
  if (!page) throw new Error('Requested native WebView2 surface was not found.');
  page.setDefaultTimeout(30000);
  page.on('pageerror', (error) => report.pageErrors.push(String(error)));
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(page.locator('.dirty-dot')).toHaveCount(0);
  await expect(button('取消当前任务')).toHaveCount(0);
  report.runtime = await bridgeCall('runtime.info');
  if (packaged) expect(report.runtime.packaged).toBe(true);
  await openProject(copies.stress);
  const before = await bridgeCall('workspace.get', { path: copies.stress });
  const land = before.datasets.find((item) => item.id === largest.inputDatasetId);
  expect(land.featureCount).toBe(largest.features);
  const landLayer = before.layers.find((item) => item.datasetId === land.id);
  await tab('地图工作区').click();
  await page.locator('.layer-select').filter({ hasText: landLayer.name }).first().click();
  await button('属性末页').click();
  await expect(page.locator('#map-panel .pagination')).toContainText(String(largest.features), { timeout: 30000 });
  const lastOffset = Math.floor((largest.features - 1) / 200) * 200;
  const tail = await bridgeCall('vector.page', { path: copies.stress, datasetId: land.id, offset: lastOffset, limit: 200, sortField: null, descending: false, filter: null });
  expect(tail.total).toBe(largest.features);
  expect(tail.hasMore).toBe(false);
  expect(tail.rows.length).toBe(largest.features - lastOffset);
  await expect(page.locator('#map-panel .pagination')).toContainText(`${lastOffset + 1}–${largest.features}`, { timeout: 30000 });
  await button('定位当前图层').click();
  await screenshot('stress-input-last-page');
  report.checks.push({ name: 'complete-input-last-page', ok: true, total: tail.total, offset: tail.offset, returned: tail.rows.length });
  await configure(largest, 'Native stress cancellation');
  await installProbe();
  await button('开始分析').click();
  const started = await latestTask(copies.stress, 'analysis');
  expect(started.status).toBe('running');
  report.stressTaskId = started.id;
  await page.evaluate(() => { window.__analysisProbe.phase = true; });
  for (let index = 0; index < 8; index++) {
    await tab('地图工作区').click();
    await expect(tab('地图工作区')).toHaveAttribute('aria-selected', 'true');
    const box = await page.locator('.map-target').boundingBox();
    if (box) {
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width / 2 + (index % 2 ? -35 : 35), box.y + box.height / 2 + 15, { steps: 4 });
      await page.mouse.up();
    }
    await tab('用地分析').click();
    await expect(tab('用地分析')).toHaveAttribute('aria-selected', 'true');
    const status = await bridgeCall('task.get', { path: copies.stress, taskId: started.id });
    expect(status.status).toBe('running');
    await sleep(200);
  }
  const cancelStarted = performance.now();
  await button('取消当前任务').click();
  const cancelled = await terminal(copies.stress, started.id, 10000);
  report.cancelTerminalMs = performance.now() - cancelStarted;
  expect(cancelled.status).toBe('cancelled');
  const metrics = await collectMetrics();
  report.metrics = metrics;
  report.measurements = { ...summarizeMetrics(metrics), cancelTerminalMs: report.cancelTerminalMs };
  await screenshot('stress-cancelled');
  expect((await bridgeCall('workspace.get', { path: copies.stress })).datasets).toEqual(before.datasets);
  report.checks.push({ name: 'running-analysis-interaction-cancel-and-no-partial-result', ok: true });
  await closeProject();
  await openProject(copies.recovery);
  const recoveryBefore = await bridgeCall('workspace.get', { path: copies.recovery });
  const previousId = recoveryBefore.tasks.find((item) => item.kind === 'analysis')?.id;
  await configure(smallest, 'Native completed recovery');
  await button('开始分析').click();
  const recoveryTask = await latestTask(copies.recovery, 'analysis', previousId);
  const completed = await terminal(copies.recovery, recoveryTask.id);
  expect(completed.status).toBe('completed');
  report.recoveryResultId = completed.datasetId;
  await expect(page.getByLabel('分析成果', { exact: true })).toHaveValue(completed.datasetId, { timeout: 60000 });
  await expect(page.getByRole('table', { name: '分类面积统计', exact: true })).toBeVisible();
  const statistics = await bridgeCall('analysis.result', { path: copies.recovery, datasetId: completed.datasetId, offset: 0, limit: 100 });
  expect(statistics.record.inputs[0].datasetId).toBe(smallest.inputDatasetId);
  await screenshot('recovery-statistics');
  for (const [label, filename] of [['导出成果 GeoPackage', 'result-export.gpkg'], ['导出统计 CSV', 'statistics-export.csv']]) {
    const prior = (await bridgeCall('workspace.get', { path: copies.recovery })).tasks.find((item) => item.kind === 'export')?.id;
    await button(label).click();
    const task = await latestTask(copies.recovery, 'export', prior);
    expect((await terminal(copies.recovery, task.id)).status).toBe('completed');
    expect((await stat(path.join(output, filename))).size).toBeGreaterThan(0);
    await expect(button(label)).toBeEnabled();
  }
  const exported = await readFile(path.join(output, 'statistics-export.csv'), 'utf8');
  expect(exported.replace(/^\uFEFF/, '').split(/\r?\n/)[0]).toContain('analysisId,inputClass,inputClassIsNull');
  expect(exported).toContain(statistics.record.id);
  const exportedInspection = await bridgeCall('source.inspect', { sourcePath: path.join(output, 'result-export.gpkg'), encoding: null });
  expect(exportedInspection.driver).toBe('GPKG');
  expect(exportedInspection.layers.find((item) => item.name === 'features')?.featureCount).toBe(statistics.record.outputFeatureCount);
  report.exportCsvBytes = Buffer.byteLength(exported);
  report.checks.push({ name: 'small-task-completes-and-both-exports-publish', ok: true, resultDatasetId: completed.datasetId });
  await closeProject();
  await openProject(copies.recovery);
  await tab('用地分析').click();
  await page.getByLabel('分析成果', { exact: true }).selectOption(completed.datasetId);
  await expect(page.getByRole('table', { name: '分类面积统计', exact: true })).toBeVisible();
  const reopened = await bridgeCall('analysis.result', { path: copies.recovery, datasetId: completed.datasetId, offset: 0, limit: 100 });
  expect(reopened).toEqual(statistics);
  await screenshot('reopened-statistics-desktop');
  await page.setViewportSize({ width: 390, height: 844 });
  await screenshot('reopened-statistics-narrow');
  await page.setViewportSize({ width: 1440, height: 900 });
  expect(report.pageErrors).toHaveLength(0);
  report.checks.push({ name: 'reopen-preserves-exact-statistics-and-provenance', ok: true });
  if (completeLarge) {
    await closeProject();
    await openProject(copies.stress);
    await configure(largest, 'Native full large completion');
    const previous = (await bridgeCall('workspace.get', { path: copies.stress })).tasks.find((item) => item.kind === 'analysis')?.id;
    await button('开始分析').click();
    const largeTask = await latestTask(copies.stress, 'analysis', previous);
    expect(largeTask.status).toBe('running');
    report.fullTaskId = largeTask.id;
    await page.evaluate(() => {
      const probe = window.__analysisProbe;
      probe.acknowledgements = []; probe.stalls = []; probe.requests = [];
      probe.activeRequests = 0; probe.peakOutstandingRequests = 0; probe.phase = true;
    });
    const fullStarted = performance.now();
    let status = largeTask;
    let iterations = 0;
    while (status.status === 'running') {
      await tab('地图工作区').click();
      await expect(tab('地图工作区')).toHaveAttribute('aria-selected', 'true');
      if (iterations % 4 === 0) {
        const box = await page.locator('.map-target').boundingBox();
        if (box) {
          await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
          await page.mouse.down();
          await page.mouse.move(box.x + box.width / 2 + (iterations % 8 ? -25 : 25), box.y + box.height / 2, { steps: 4 });
          await page.mouse.up();
        }
      }
      await sleep(200);
      await tab('用地分析').click();
      await expect(tab('用地分析')).toHaveAttribute('aria-selected', 'true');
      status = await bridgeCall('task.get', { path: copies.stress, taskId: largeTask.id });
      iterations++;
      if (iterations % 20 === 0) {
        report.fullLive = { iterations, elapsedMs: performance.now() - fullStarted, stage: status.stage, completed: status.completed, total: status.total };
        await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
        console.log(JSON.stringify({ phase: 'full-large', ...report.fullLive }));
      }
      if (performance.now() - fullStarted > 950000) throw new Error('Full large task did not stop within harness deadline.');
      if (status.status === 'running') await sleep(600);
    }
    report.fullDurationMs = performance.now() - fullStarted;
    report.fullIterations = iterations;
    report.fullMetrics = await collectMetrics();
    report.fullMeasurements = summarizeMetrics(report.fullMetrics);
    report.fullTask = status;
    expect(status.status).toBe('completed');
    await expect(page.getByLabel('分析成果', { exact: true })).toHaveValue(status.datasetId, { timeout: 60000 });
    const fullPage = await bridgeCall('analysis.result', { path: copies.stress, datasetId: status.datasetId, offset: 0, limit: 100 });
    const baseline = await bridgeCall('analysis.result', { path: copies.stress, datasetId: largest.resultDatasetId, offset: 0, limit: 100 });
    expect(fullPage.record.outputFeatureCount).toBe(largest.features);
    expect(fullPage.record.coveredAreaM2).toBe(baseline.record.coveredAreaM2);
    expect(fullPage.total).toBe(baseline.total);
    expect(fullPage.rows).toEqual(baseline.rows);
    await screenshot('full-large-statistics');
    await closeProject();
    await openProject(copies.stress);
    await tab('用地分析').click();
    await page.getByLabel('分析成果', { exact: true }).selectOption(status.datasetId);
    const fullReopened = await bridgeCall('analysis.result', { path: copies.stress, datasetId: status.datasetId, offset: 0, limit: 100 });
    expect(fullReopened).toEqual(fullPage);
    await screenshot('full-large-reopened-statistics');
    for (const [name, maximum] of Object.entries(thresholds)) if (name !== 'cancelTerminalMs') expect(report.fullMeasurements[name], `full ${name}`).toBeLessThanOrEqual(maximum);
    report.checks.push({ name: 'full-large-completion-with-continuous-interaction-and-reopen', ok: true, features: fullPage.record.outputFeatureCount, resultDatasetId: status.datasetId, iterations });
  }
  expect(report.pageErrors).toHaveLength(0);
  for (const [name, maximum] of Object.entries(thresholds)) expect(report.measurements[name], name).toBeLessThanOrEqual(maximum);
  report.ok = true;
} catch (error) {
  report.error = String(error.stack || error);
  if (page) { try { await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }); report.screenshots.push('failure.png'); } catch {} }
  process.exitCode = 1;
} finally {
  if (page) {
    try { await page.evaluate(() => { const probe = window.__analysisProbe; if (probe) { clearInterval(probe.timer); document.removeEventListener('click', probe.click, true); probe.restore.forEach((restore) => restore()); } window.__analysisPickerRestore?.(); }); } catch {}
  }
  report.finishedAt = new Date().toISOString();
  await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ok: report.ok, output, measurements: report.measurements, fullMeasurements: report.fullMeasurements, fullDurationMs: report.fullDurationMs, error: report.error }, null, 2));
  await browser?.close();
}
