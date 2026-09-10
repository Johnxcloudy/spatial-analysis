import { createRequire } from 'node:module';
import { appendFile, copyFile, cp, mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(root, 'apps/desktop/package.json'));
const { chromium, expect } = require('@playwright/test');
const arg = (name, fallback) => { const index = process.argv.indexOf(name); return index < 0 ? fallback : process.argv[index + 1]; };
const fixturePath = arg('--fixtures');
if (!fixturePath) throw new Error('--fixtures must name a successful portability-verification.json');
const fixture = JSON.parse(await readFile(fixturePath, 'utf8'));
if (!fixture.ok) throw new Error('Source portability verification must pass first.');
const output = path.join(root, '.artifacts', `portability-ui-${Date.now()}`);
await mkdir(output);
const sourceRoot = path.join(output, 'original');
await cp(path.dirname(fixture.originalProject), sourceRoot, { recursive: true, errorOnExist: true, force: false });
const originalPath = path.join(sourceRoot, 'project.spa');
const copiedPath = path.join(output, 'portable-copy', 'project.spa');
const browser = await chromium.connectOverCDP(arg('--cdp', 'http://127.0.0.1:9224'));
const page = browser.contexts()[0].pages().find((candidate) => candidate.url().includes('1420'));
if (!page) throw new Error('Native development WebView2 was not found.');
page.setDefaultTimeout(20000);
const errors = [];
page.on('pageerror', (error) => errors.push(String(error)));
const report = { ok: false, checks: [], screenshots: [], errors, originalPath, copiedPath };
const button = (name) => page.getByRole('button', { name, exact: true });
const sourcePanel = () => page.getByRole('region', { name: '来源位置', exact: true });

async function bridgeCall(method, params) {
  return page.evaluate(async ({ method, params }) => {
    const address = performance.getEntriesByType('resource').map((entry) => entry.name).find((name) => new URL(name).pathname === '/src/bridge.ts') ?? '/src/bridge.ts';
    const { desktop } = await import(address);
    return desktop.request(method, params);
  }, { method, params });
}

async function pickers(projectPath = originalPath, sourcePath = '') {
  await page.evaluate(async ({ projectPath, sourcePath, parent }) => {
    const addresses = performance.getEntriesByType('resource').map((entry) => entry.name).filter((name) => new URL(name).pathname === '/src/bridge.ts');
    for (const address of new Set(addresses.length ? addresses : ['/src/bridge.ts'])) {
      const { desktop } = await import(address);
      desktop.chooseProject = () => Promise.resolve(projectPath);
      desktop.chooseParent = () => Promise.resolve(parent);
      desktop.chooseSource = () => Promise.resolve(sourcePath);
    }
  }, { projectPath, sourcePath, parent: output });
}

async function screenshot(name, width, height) {
  await page.setViewportSize({ width, height });
  await page.evaluate(() => document.fonts.ready);
  await expect(page.locator('.map-loading')).toHaveCount(0, { timeout: 30000 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  const session = await page.context().newCDPSession(page);
  try {
    const contentHeight = await page.evaluate(() => Math.max(innerHeight, document.documentElement.scrollHeight));
    const capture = await session.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true, clip: { x: 0, y: 0, width, height: contentHeight, scale: 1 } });
    await writeFile(path.join(output, `${name}.png`), Buffer.from(capture.data, 'base64'));
  } finally { await session.detach(); }
  report.screenshots.push(`${name}.png`);
}

async function mapPixels() {
  return page.locator('.vector-map canvas').evaluateAll((canvases) => canvases.reduce((sum, canvas) => {
    if (!canvas.width || !canvas.height) return sum;
    const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    for (let index = 3; index < data.length; index += 4) if (data[index]) sum++;
    return sum;
  }, 0));
}

async function saveDialog(name) {
  await button('项目另存为').click();
  const dialog = page.getByRole('dialog', { name: '项目另存为', exact: true });
  await dialog.getByLabel('新目录名称', { exact: true }).fill(name);
  await dialog.getByRole('button', { name: '选择父目录', exact: true }).click();
  return dialog;
}

try {
  await page.setViewportSize({ width: 1440, height: 900 });
  await pickers();
  await button('打开项目').click();
  await expect(page.locator('.header-project')).toContainText('Mixed original');
  const before = await bridgeCall('workspace.get', { path: originalPath });
  const raster = before.datasets.find((dataset) => dataset.kind === 'raster' && dataset.name === 'rgb');
  if (!raster) throw new Error('Expected rgb dataset in the mixed fixture.');
  const layer = before.layers.find((item) => item.datasetId === raster.id);
  await page.locator('.layer-select').filter({ hasText: layer.name }).click();
  await button('定位当前图层').click();
  await expect.poll(mapPixels).toBeGreaterThan(100);
  await button('数据与检查报告').click();
  await expect(sourcePanel()).toContainText('来源存在（当前内容未核对）');
  const previousSource = await bridgeCall('source.status', { path: originalPath, datasetId: raster.id });
  const candidate = path.join(output, 'relocated-rgb.tif');
  const bad = path.join(output, 'changed-rgb.tif');
  await copyFile(previousSource.resolvedPath, candidate);
  await copyFile(candidate, bad);
  await appendFile(bad, Buffer.from([0]));
  await page.getByRole('tab', { name: '项目', exact: true }).click();
  const draft = '另存为应保留这段未保存的项目描述';
  await page.getByRole('textbox', { name: '项目描述', exact: true }).fill(draft);
  await pickers(originalPath, bad);
  await button('重新定位来源').click();
  await expect(page.locator('.task-strip > strong')).toHaveText('来源定位');
  await expect(page.locator('.task-strip .danger')).toBeVisible({ timeout: 90000 });
  await expect(page.getByRole('textbox', { name: '项目描述', exact: true })).toHaveValue(draft);
  expect((await bridgeCall('source.status', { path: originalPath, datasetId: raster.id })).resolvedPath).toBe(previousSource.resolvedPath);
  report.checks.push('Source mismatch reports failure and preserves the original location and unsaved project draft.');

  await pickers(originalPath, candidate);
  await button('重新定位来源').click();
  await expect(sourcePanel()).toContainText(candidate, { timeout: 90000 });
  await expect(sourcePanel()).toContainText('上次身份核对（历史记录）');
  await screenshot('relocation-desktop', 1440, 900);
  await screenshot('relocation-narrow', 390, 844);
  await page.setViewportSize({ width: 1440, height: 900 });
  report.checks.push('Explicit native source relocation updates the resolved path and displays historical verification separately.');

  const existing = path.join(output, 'existing');
  await mkdir(existing);
  await writeFile(path.join(existing, 'keep.txt'), 'preserved');
  let dialog = await saveDialog('existing');
  await dialog.getByRole('button', { name: '另存项目', exact: true }).click();
  await expect(dialog.getByRole('alert')).toBeVisible();
  await dialog.locator('.modal-actions').getByRole('button', { name: '取消', exact: true }).click();
  expect(await readFile(path.join(existing, 'keep.txt'), 'utf8')).toBe('preserved');
  await expect(page.getByRole('textbox', { name: '项目描述', exact: true })).toHaveValue(draft);
  report.checks.push('An existing Save As destination is refused without changing its contents or the project draft.');

  dialog = await saveDialog('portable-copy');
  await expect(dialog.getByRole('alert')).toHaveCount(0);
  await screenshot('save-as-desktop', 1440, 900);
  await screenshot('save-as-narrow', 390, 844);
  await page.setViewportSize({ width: 1440, height: 900 });
  await dialog.getByRole('button', { name: '另存项目', exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(page.locator('.status-message')).toContainText('项目已另存并打开', { timeout: 90000 });
  await expect.poll(async () => {
    try { return (await bridgeCall('workspace.get', { path: copiedPath })).datasets.length; }
    catch { return 0; }
  }, { timeout: 90000 }).toBe(before.datasets.length);
  const copied = await bridgeCall('workspace.get', { path: copiedPath });
  expect(copied.projectId).not.toBe(before.projectId);
  expect(copied.datasets).toEqual(before.datasets);
  expect(copied.layers).toEqual(before.layers);
  await expect(page.getByRole('textbox', { name: '项目描述', exact: true })).toHaveValue(draft);
  report.checks.push('Successful Save As opens a new identity, preserves the current draft, and copies all dataset versions and layer settings.');

  await page.getByRole('tab', { name: '图层', exact: true }).click();
  await page.locator('.layer-select').filter({ hasText: layer.name }).click();
  await button('定位当前图层').click();
  await expect.poll(mapPixels).toBeGreaterThan(100);
  report.visibleCanvasPixels = await mapPixels();
  await screenshot('copied-workspace-desktop', 1440, 900);
  await screenshot('copied-workspace-narrow', 390, 844);
  await page.setViewportSize({ width: 1440, height: 900 });
  if (await button('保存项目').isEnabled()) await button('保存项目').click();
  await button('关闭项目').click();
  const movedRoot = path.join(output, 'moved-copy');
  await rename(path.dirname(copiedPath), movedRoot);
  const movedPath = path.join(movedRoot, 'project.spa');
  await pickers(movedPath, candidate);
  await button('打开项目').click();
  await expect.poll(async () => {
    try { return (await bridgeCall('workspace.get', { path: movedPath })).datasets.length; }
    catch { return 0; }
  }).toBe(before.datasets.length);
  const derived = before.datasets.find((dataset) => dataset.source.driver === 'TablePoints');
  const derivedLayer = before.layers.find((item) => item.datasetId === derived.id);
  await page.locator('.layer-select').filter({ hasText: derivedLayer.name }).click();
  await button('定位当前图层').click();
  await expect.poll(mapPixels).toBeGreaterThan(10);
  await button('数据与检查报告').click();
  await expect(sourcePanel()).toContainText('项目内部来源');
  await expect(sourcePanel()).toContainText(movedRoot);
  await expect(button('重新定位来源')).toBeDisabled();
  report.movedProject = movedPath;
  report.checks.push('After moving the whole copied project, the native UI reopens it and resolves derived-point provenance inside its new directory.');
  await screenshot('moved-internal-source-desktop', 1440, 900);
  await button('关闭项目').click();
  const pending = page.getByRole('dialog', { name: '项目有未保存的更改' });
  if (await pending.isVisible()) await pending.getByRole('button', { name: '放弃更改' }).click();
  const original = await bridgeCall('project.open', { path: originalPath });
  expect(original.description).not.toBe(draft);
  await bridgeCall('project.close');
  expect(errors).toEqual([]);
  report.ok = true;
} catch (error) {
  report.error = String(error.stack ?? error);
  throw error;
} finally {
  await writeFile(path.join(output, 'verification.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ok: report.ok, checks: report.checks.length, output }));
  await browser.close();
}
