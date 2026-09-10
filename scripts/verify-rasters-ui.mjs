import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(root, 'apps/desktop/package.json'));
const { chromium, expect } = require('@playwright/test');
const arg = (name, fallback) => { const index = process.argv.indexOf(name); return index < 0 ? fallback : process.argv[index + 1]; };
const fixturePath = arg('--fixtures');
if (!fixturePath) throw new Error('--fixtures must name a successful verify-rasters.py report.');
const fixtures = JSON.parse(await readFile(fixturePath, 'utf8'));
if (!fixtures.ok || !fixtures.exportDirectory) throw new Error('Raster source acceptance has not passed.');
const sources = Object.fromEntries(['gray', 'rgb', 'unknown', 'rotated'].map((name) => [name, path.join(fixtures.exportDirectory, `${name}.tiff`)]));
const output = path.join(root, '.artifacts', `rasters-ui-${Date.now()}`);
await mkdir(output);
const browser = await chromium.connectOverCDP(arg('--cdp', 'http://127.0.0.1:9224'));
const page = browser.contexts()[0].pages().find((candidate) => candidate.url().includes('1420'));
if (!page) throw new Error('Native development WebView2 was not found.');
page.setDefaultTimeout(20000);
const errors = [];
page.on('pageerror', (error) => errors.push(String(error)));
const result = { ok: false, checks: [], screenshots: [], pixels: [], errors };
const button = (name) => page.getByRole('button', { name, exact: true });
const parent = path.join(output, 'projects');
const projectName = 'GeoTIFF 验收';
const projectPath = path.join(parent, projectName, 'project.spa');
const exportPath = path.join(output, 'gray-export.tif');
const vectorPath = path.join(output, 'overlay.geojson');
const draft = '栅格导入、渲染、查询期间保留的项目草稿';
await mkdir(parent);
const lonlat = (x, y) => [x / 6378137 * 180 / Math.PI, (2 * Math.atan(Math.exp(y / 6378137)) - Math.PI / 2) * 180 / Math.PI];
const ring = [[12600000, 3150000], [12600400, 3150000], [12600400, 3150400], [12600000, 3150400], [12600000, 3150000]].map(([x, y]) => lonlat(x, y));
await writeFile(vectorPath, JSON.stringify({ type: 'FeatureCollection', features: [{ type: 'Feature', properties: { name: 'Synthetic overlay' }, geometry: { type: 'Polygon', coordinates: [ring] } }] }));

async function pickers(source) {
  await page.evaluate(async ({ parent, projectPath, source, exportPath, vectorPath }) => {
    const addresses = performance.getEntriesByType('resource').map((entry) => entry.name).filter((name) => new URL(name).pathname === '/src/bridge.ts');
    for (const address of new Set(addresses.length ? addresses : ['/src/bridge.ts'])) {
      const { desktop } = await import(address);
      desktop.chooseParent = () => Promise.resolve(parent);
      desktop.chooseProject = () => Promise.resolve(projectPath);
      desktop.chooseRaster = () => Promise.resolve(source);
      desktop.chooseRasterExport = () => Promise.resolve(exportPath);
      desktop.chooseVector = () => Promise.resolve(vectorPath);
    }
  }, { parent, projectPath, source, exportPath, vectorPath });
}

async function rpc(method, params) {
  return page.evaluate(async ({ method, params }) => {
    const address = performance.getEntriesByType('resource').map((entry) => entry.name).find((name) => new URL(name).pathname === '/src/bridge.ts') ?? '/src/bridge.ts';
    const { desktop } = await import(address);
    return desktop.request(method, params);
  }, { method, params });
}
const workspace = () => rpc('workspace.get', { path: projectPath });

async function pixels() {
  return page.locator('.vector-map canvas').evaluateAll((canvases) => canvases.map((canvas, index) => {
    const rect = canvas.getBoundingClientRect();
    if (!canvas.width || !canvas.height || !rect.width) return null;
    const { width, height } = canvas;
    const data = canvas.getContext('2d').getImageData(0, 0, width, height).data;
    let count = 0, colored = 0, minX = width, minY = height, maxX = -1, maxY = -1, hash = 0;
    const colors = new Set();
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
      const index = (y * width + x) * 4;
      if (!data[index + 3]) continue;
      count++;
      if (data[index] !== data[index + 1] || data[index + 1] !== data[index + 2]) colored++;
      minX = Math.min(minX, x); minY = Math.min(minY, y); maxX = Math.max(maxX, x); maxY = Math.max(maxY, y);
      hash = (Math.imul(hash, 31) + data[index] + data[index + 1] * 257 + data[index + 2] * 65537 + data[index + 3]) >>> 0;
      if (colors.size < 40) colors.add(`${data[index]},${data[index + 1]},${data[index + 2]},${data[index + 3]}`);
    }
    return { index, width, height, count, colored, hash, colors: [...colors], minX, minY, maxX, maxY, touchesEdge: count > 0 && (minX < 3 || minY < 3 || maxX >= width - 3 || maxY >= height - 3), rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height } };
  }).filter(Boolean));
}

async function stableMap({ blank = false, framed = false } = {}) {
  let previous = '', stable = 0;
  await expect.poll(async () => {
    const current = await pixels();
    const signature = JSON.stringify(current.map(({ count, hash, rect }) => ({ count, hash, width: rect.width, height: rect.height })));
    stable = signature === previous ? stable + 1 : 0;
    previous = signature;
    const total = current.reduce((sum, canvas) => sum + canvas.count, 0);
    return stable >= 3 && (blank ? total === 0 : total > 20) && (!framed || !current.some((canvas) => canvas.touchesEdge)) && await page.locator('.map-loading').count() === 0;
  }, { intervals: [150], timeout: 30000 }).toBe(true);
  return pixels();
}

async function screenshot(name, width, height, map = false) {
  await page.setViewportSize({ width, height });
  await page.evaluate(() => document.fonts.ready);
  if (map) { await button('定位当前图层').click(); await stableMap({ framed: true }); }
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  const session = await page.context().newCDPSession(page);
  try {
    const contentHeight = await page.evaluate(() => Math.max(innerHeight, document.documentElement.scrollHeight));
    const capture = await session.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true, clip: { x: 0, y: 0, width, height: contentHeight, scale: 1 } });
    await writeFile(path.join(output, `${name}.png`), Buffer.from(capture.data, 'base64'));
  } finally { await session.detach(); }
  result.screenshots.push(`${name}.png`);
}

async function task(kind) {
  await expect(page.locator('.task-strip > strong')).toHaveText(kind);
  await expect(page.locator('.task-strip .task-stage')).toHaveText('已完成', { timeout: 90000 });
  await expect(button('导入栅格')).toBeEnabled();
}

async function importRaster(name, capture = false) {
  console.log(`Inspecting and importing ${name}.tiff through the native bridge.`);
  await pickers(sources[name]);
  await button('导入栅格').click();
  const dialog = page.getByRole('dialog', { name: '导入 GeoTIFF' });
  await dialog.getByRole('button', { name: '选择栅格文件', exact: true }).click();
  await expect(dialog.getByRole('region', { name: '栅格元数据' })).toContainText('4 × 4');
  if (name === 'unknown') await expect(dialog).toContainText('来源 CRS 未知');
  if (capture) {
    await expect(dialog).toContainText('Synthetic elevation');
    await dialog.locator('.raster-metadata summary').first().click();
    await expect(dialog).toContainText('Scale / Offset');
    await expect(dialog).toContainText('抽样最小/最大');
    await screenshot('gray-import-desktop', 1440, 900);
    await screenshot('gray-import-narrow', 390, 844);
    await page.setViewportSize({ width: 1440, height: 900 });
  }
  await dialog.getByRole('button', { name: '导入栅格', exact: true }).click();
  await expect(dialog).toBeHidden();
  await task('数据导入');
  await expect(page.getByRole('region', { name: '像元信息' })).toContainText(name);
}

async function clickCell(row, column) {
  await page.locator('.vector-map').scrollIntoViewIfNeeded();
  const canvases = await stableMap({ framed: true });
  const canvas = canvases.find((item) => item.count > 0);
  if (!canvas) throw new Error('No raster canvas to identify a cell.');
  const x = canvas.minX + (column + 0.5) * (canvas.maxX - canvas.minX + 1) / 4;
  const y = canvas.minY + (row + 0.5) * (canvas.maxY - canvas.minY + 1) / 4;
  await page.mouse.click(canvas.rect.x + x * canvas.rect.width / canvas.width, canvas.rect.y + y * canvas.rect.height / canvas.height);
  await expect(page.getByRole('region', { name: '像元信息' })).toContainText(`行 ${row} · 列 ${column}（从 0 开始）`);
}

async function gridPixels(canvases) {
  const visible = canvases.find((canvas) => canvas.count > 0);
  if (!visible) throw new Error('No raster canvas to read grid pixels.');
  return page.locator('.vector-map canvas').nth(visible.index).evaluate((canvas, bounds) => {
    const context = canvas.getContext('2d');
    return Array.from({ length: 4 }, (_, row) => Array.from({ length: 4 }, (_, column) => {
      const x = Math.floor(bounds.minX + (column + 0.5) * (bounds.maxX - bounds.minX + 1) / 4);
      const y = Math.floor(bounds.minY + (row + 0.5) * (bounds.maxY - bounds.minY + 1) / 4);
      return [...context.getImageData(x, y, 1, 1).data];
    }));
  }, visible);
}

async function setRange(channel, low, high) {
  await page.getByLabel(`${channel} 最小值`, { exact: true }).fill(String(low));
  await page.getByLabel(`${channel} 最大值`, { exact: true }).fill(String(high));
}

try {
  await page.reload({ waitUntil: 'networkidle' });
  await expect(page.getByText('引擎已连接', { exact: true })).toBeVisible({ timeout: 90000 });
  await page.setViewportSize({ width: 1440, height: 900 });
  await pickers(sources.gray);
  await button('新建项目').click();
  const create = page.getByRole('dialog');
  await create.getByLabel('项目名称').fill(projectName);
  await create.getByRole('button', { name: '选择父目录', exact: true }).click();
  await create.getByRole('button', { name: '创建项目', exact: true }).click();
  await expect(create).toBeHidden();
  await page.getByRole('tab', { name: '项目', exact: true }).click();
  await page.getByLabel('项目描述').fill(draft);
  await importRaster('gray', true);
  await expect(page.getByLabel('项目描述')).toHaveValue(draft);
  await page.getByRole('tab', { name: '图层', exact: true }).click();
  await expect(page.locator('.layer-select')).toHaveCount(1);
  await expect(page.locator('.attribute-section')).toHaveCount(0);
  await setRange('灰度', 0, 15);
  if (await button('应用栅格显示设置').isEnabled()) await button('应用栅格显示设置').click();
  await button('定位当前图层').click();
  const grayPixels = await stableMap({ framed: true });
  expect(grayPixels.reduce((sum, canvas) => sum + canvas.colored, 0)).toBe(0);
  expect(grayPixels.flatMap((canvas) => canvas.colors)).toContain('0,0,0,255');
  const grayGrid = await gridPixels(grayPixels);
  expect(grayGrid[0][0][3]).toBe(0);
  expect(grayGrid[3][0][3]).toBe(0);
  expect(grayGrid[0][1]).toEqual([0, 0, 0, 255]);
  expect(grayGrid[3][3]).toEqual([255, 255, 255, 255]);
  result.pixels.push({ name: 'gray', canvases: grayPixels, cells: grayGrid });
  await clickCell(0, 1);
  let cells = page.getByRole('region', { name: '像元信息' }).locator('tbody tr').first().locator('td');
  await expect(cells.nth(0)).toHaveText('0.0');
  await expect(cells.nth(1)).toHaveText('100');
  await expect(cells.nth(2)).toHaveText('m');
  await expect(cells.nth(3)).toHaveText('是');
  await clickCell(0, 0);
  await expect(cells.nth(0)).toHaveText('-9999.0');
  await expect(cells.nth(3)).toHaveText('否');
  await clickCell(3, 0);
  await expect(cells.nth(0)).toHaveText('12.0');
  await expect(cells.nth(3)).toHaveText('否');
  await screenshot('gray-pixels-desktop', 1440, 900, true);
  await screenshot('gray-pixels-medium', 900, 900, true);
  await screenshot('gray-pixels-narrow', 390, 844, true);
  await page.setViewportSize({ width: 1440, height: 900 });
  await button('定位当前图层').click();
  await stableMap({ framed: true });
  const zoomBefore = (await pixels()).reduce((sum, canvas) => sum + canvas.count, 0);
  await button('地图放大').click();
  await expect.poll(async () => (await pixels()).reduce((sum, canvas) => sum + canvas.count, 0)).toBeGreaterThan(zoomBefore);
  await stableMap();
  await button('地图缩小').click();
  await button('定位当前图层').click();
  await stableMap({ framed: true });
  await button('隐藏 gray').click();
  await stableMap({ blank: true });
  await button('显示 gray').click();
  await stableMap({ framed: true });
  await button('导出当前数据').click();
  await task('数据导出');
  const hash = (buffer) => createHash('sha256').update(buffer).digest('hex');
  expect(hash(await readFile(exportPath))).toBe(hash(await readFile(sources.gray)));
  expect((await rpc('raster.inspect', { sourcePath: exportPath })).raster.width).toBe(4);
  result.checks.push('Gray import metadata, raw zero, scale/offset/unit, NoData/mask cells, framed gray pixels, zoom, visibility and exact GeoTIFF export passed.');

  await importRaster('rgb');
  await button('RGB').click();
  for (const [index, channel] of ['R', 'G', 'B'].entries()) {
    await page.getByLabel(`${channel} 波段`, { exact: true }).selectOption(String(index + 1));
    await setRange(channel, 0, 255);
  }
  await button('应用栅格显示设置').click();
  await button('隐藏 gray').click();
  await button('定位当前图层').click();
  const rgbPixels = await stableMap({ framed: true });
  expect(rgbPixels.flatMap((canvas) => canvas.colors)).toContain('40,120,220,255');
  result.pixels.push({ name: 'rgb', canvases: rgbPixels });
  await page.getByLabel('图层不透明度').focus();
  await page.getByLabel('图层不透明度').press('Home');
  await expect.poll(async () => (await workspace()).layers.find((layer) => layer.name === 'rgb')?.opacity).toBe(0);
  for (let step = 1; step <= 11; step++) {
    await expect(page.getByLabel('图层不透明度')).toBeEnabled();
    await page.getByLabel('图层不透明度').focus();
    await page.getByLabel('图层不透明度').press('ArrowRight');
    await expect.poll(async () => (await workspace()).layers.find((layer) => layer.name === 'rgb')?.opacity).toBe(step / 20);
  }
  await expect.poll(async () => (await workspace()).layers.find((layer) => layer.name === 'rgb')?.opacity).toBe(0.55);
  await screenshot('rgb-style-desktop', 1440, 900, true);
  await screenshot('rgb-style-narrow', 390, 844, true);
  await page.setViewportSize({ width: 1440, height: 900 });
  await button('数据与检查报告').click();
  await expect(page.getByRole('complementary', { name: '数据与检查报告' })).toContainText('3');
  await page.getByRole('complementary', { name: '数据与检查报告' }).locator('.raster-metadata summary').first().click();
  await screenshot('rgb-report-desktop', 1440, 900);
  await button('数据与检查报告').click();
  result.checks.push('Explicit RGB bands/ranges render the expected channels; shared opacity persists and desktop/narrow styles and metadata remain usable.');

  await importRaster('unknown');
  await expect(button('识别像元')).toBeDisabled();
  await expect(button('定位当前图层')).toBeDisabled();
  await expect(button('导出当前数据')).toBeEnabled();
  await expect(page.getByRole('region', { name: '像元信息' })).toContainText('来源 CRS 未知，不能按位置查询');
  await screenshot('unknown-crs-desktop', 1440, 900);
  await importRaster('rotated');
  await button('隐藏 rgb').click();
  await button('定位当前图层').click();
  await stableMap({ framed: true });
  await screenshot('rotated-grid-desktop', 1440, 900, true);
  await button('隐藏 rotated').click();
  await button('显示 rgb').click();
  await pickers(sources.rgb);
  await button('导入数据').click();
  const vectorDialog = page.getByRole('dialog', { name: '导入矢量数据' });
  await vectorDialog.getByRole('button', { name: '选择文件', exact: true }).click();
  await expect(vectorDialog.getByRole('button', { name: '导入所选数据层', exact: true })).toBeEnabled();
  await vectorDialog.getByRole('button', { name: '导入所选数据层', exact: true }).click();
  await expect(vectorDialog).toBeHidden();
  await task('数据导入');
  await expect(page.locator('.attribute-section tbody tr')).toHaveCount(1);
  await expect(page.locator('.attribute-section')).toContainText('Synthetic overlay');
  await page.locator('.layer-select').filter({ hasText: /^rgb/ }).click();
  await button('定位当前图层').click();
  await stableMap({ framed: true });
  await button('识别像元').click();
  const state = await workspace();
  const overlay = state.layers.find((layer) => !layer.rasterStyle);
  if (!overlay) throw new Error('Vector overlay was not imported.');
  for (let index = state.layers.findIndex((layer) => layer.id === overlay.id); index > 0; index--) {
    await button(`上移 ${overlay.name}`).click();
    await expect.poll(async () => (await workspace()).layers.findIndex((layer) => layer.id === overlay.id)).toBe(index - 1);
  }
  await page.locator('.layer-select').filter({ hasText: overlay.name }).click();
  await page.locator('.attribute-section tbody tr').first().click();
  await expect(page.locator('.attribute-section tbody tr.selected')).toHaveCount(1);
  await screenshot('mixed-overlay-desktop', 1440, 900, true);
  await screenshot('mixed-overlay-narrow', 390, 844, true);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole('tab', { name: '项目', exact: true }).click();
  await expect(page.getByLabel('项目描述')).toHaveValue(draft);
  await button('保存项目').click();
  await expect(page.getByText('项目已保存', { exact: true })).toBeVisible();
  const saved = await workspace();
  await button('关闭项目').click();
  await button('打开项目').click();
  await expect(page.getByLabel('项目描述')).toHaveValue(draft);
  await page.getByRole('tab', { name: '图层', exact: true }).click();
  await expect(page.locator('.layer-select')).toHaveCount(5);
  const reopened = await workspace();
  expect(reopened.layers).toEqual(saved.layers);
  expect(reopened.datasets).toEqual(saved.datasets);
  expect(reopened.layers.find((layer) => layer.name === 'rgb')).toMatchObject({ opacity: 0.55, rasterStyle: { mode: 'rgb', bands: [1, 2, 3], ranges: [[0, 255], [0, 255], [0, 255]], resampling: 'nearest' } });
  result.checks.push('Unknown CRS disables geographic actions; rotated grids render; vector overlay selection, mixed order, styles and project draft survive save/reopen.');
  await button('关闭项目').click();
  expect(errors).toEqual([]);
  result.ok = true;
} catch (error) {
  result.error = String(error);
  await screenshot('failure', 1440, 900).catch(() => {});
  throw error;
} finally {
  await writeFile(path.join(output, 'verification.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ ...result, output }));
  await browser.close();
}
