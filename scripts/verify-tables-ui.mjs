import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(root, 'apps/desktop/package.json'));
const { chromium, expect } = require('@playwright/test');
const arg = (name, fallback) => { const i = process.argv.indexOf(name); return i < 0 ? fallback : process.argv[i + 1]; };
const output = path.join(root, '.artifacts', `tables-ui-${Date.now()}`);
await mkdir(output);
const sources = JSON.parse(await readFile(arg('--fixtures', path.join(root, '.artifacts/table-ui-fixtures/manifest.json')), 'utf8'));
const browser = await chromium.connectOverCDP(arg('--cdp', 'http://127.0.0.1:9224'));
const page = browser.contexts()[0].pages().find((candidate) => candidate.url().includes('1420'));
if (!page) throw new Error('Native development WebView2 was not found.');
page.setDefaultTimeout(20000);
const errors = [];
page.on('pageerror', (error) => errors.push(String(error)));
const result = { ok: false, checks: [], screenshots: [], errors };
const button = (name) => page.getByRole('button', { name, exact: true });
const parent = path.join(output, 'projects');
const projectName = '坐标表验收';
const projectPath = path.join(parent, projectName, 'project.spa');
await mkdir(parent);

async function pickers(source, exportPath = path.join(output, 'table-export.gpkg')) {
  await page.evaluate(async ({ parent, projectPath, source, exportPath }) => {
    const addresses = performance.getEntriesByType('resource').map((entry) => entry.name)
      .filter((name) => new URL(name).pathname === '/src/bridge.ts');
    for (const address of new Set(addresses.length ? addresses : ['/src/bridge.ts'])) {
      const { desktop } = await import(address);
      desktop.chooseParent = () => Promise.resolve(parent);
      desktop.chooseProject = () => Promise.resolve(projectPath);
      desktop.selectTableSource = () => Promise.resolve(source);
      desktop.chooseExport = () => Promise.resolve(exportPath);
    }
  }, { parent, projectPath, source, exportPath });
}

async function screenshot(name, width, height, map = false) {
  await page.setViewportSize({ width, height });
  await page.evaluate(() => document.fonts.ready);
  if (map) {
    await button('定位当前图层').click();
    let previous = -1, stable = 0;
    await expect.poll(async () => {
      const current = await pixels();
      stable = current.colored === previous ? stable + 1 : 0;
      previous = current.colored;
      return stable >= 3 && current.colored > 20 && !current.touchesEdge;
    }, { intervals: [150] }).toBe(true);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  const session = await page.context().newCDPSession(page);
  try {
    const contentHeight = await page.evaluate(() => Math.max(innerHeight, document.documentElement.scrollHeight));
    const capture = await session.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true,
      clip: { x: 0, y: 0, width, height: contentHeight, scale: 1 } });
    await writeFile(path.join(output, `${name}.png`), Buffer.from(capture.data, 'base64'));
  } finally { await session.detach(); }
  result.screenshots.push(`${name}.png`);
}

async function task(kind) {
  await expect(page.locator('.task-strip > strong')).toHaveText(kind);
  await expect(page.locator('.task-strip .task-stage')).toHaveText('已完成', { timeout: 90000 });
  await expect(button('导入表格')).toBeEnabled();
}

async function pixels() {
  return page.locator('.vector-map canvas').evaluateAll((canvases) => {
    let colored = 0, touchesEdge = false;
    for (const canvas of canvases) {
      if (!canvas.width || !canvas.height || !canvas.getBoundingClientRect().width) continue;
      const { width, height } = canvas;
      const data = canvas.getContext('2d').getImageData(0, 0, width, height).data;
      for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
        if (!data[(y * width + x) * 4 + 3]) continue;
        colored++;
        if (x < 3 || y < 3 || x >= width - 3 || y >= height - 3) touchesEdge = true;
      }
    }
    return { colored, touchesEdge };
  });
}

try {
  await page.reload({ waitUntil: 'networkidle' });
  await expect(page.getByText('引擎已连接', { exact: true })).toBeVisible({ timeout: 90000 });
  await page.setViewportSize({ width: 1440, height: 900 });
  await pickers(sources[0].sourcePath);
  await button('新建项目').click();
  const create = page.getByRole('dialog');
  await create.getByLabel('项目名称').fill(projectName);
  await create.getByRole('button', { name: '选择父目录', exact: true }).click();
  await create.getByRole('button', { name: '创建项目', exact: true }).click();
  await expect(create).toBeHidden();
  await page.getByRole('tab', { name: '项目', exact: true }).click();
  await page.getByLabel('项目描述', { exact: true }).fill('表格导入与转点期间保留的项目草稿');
  await button('导入表格').click();
  let dialog = page.getByRole('dialog', { name: '导入坐标表' });
  await dialog.getByRole('button', { name: '选择表格文件', exact: true }).click();
  await expect(dialog.getByRole('region', { name: '表格预览' })).toContainText('001');
  await screenshot('csv-import-desktop', 1440, 900);
  await screenshot('csv-import-narrow', 390, 844);
  await page.setViewportSize({ width: 1440, height: 900 });
  await dialog.getByRole('button', { name: '导入表格', exact: true }).click();
  await expect(dialog).toBeHidden();
  await task('数据导入');
  await expect(page.getByLabel('项目描述')).toHaveValue('表格导入与转点期间保留的项目草稿');
  await page.getByRole('tab', { name: '图层', exact: true }).click();
  await expect(page.locator('.table-select')).toHaveCount(1);
  await expect(page.locator('.layer-select')).toHaveCount(0);
  await expect(page.locator('.attribute-section tbody tr')).toHaveCount(6);
  await expect(page.locator('.vector-map')).toHaveCount(0);
  await page.getByLabel('筛选字段').selectOption('code');
  await page.getByLabel('筛选条件').selectOption('equals');
  await page.getByLabel('筛选值').fill('002');
  await button('应用筛选').click();
  await expect(page.locator('.attribute-section tbody tr')).toHaveCount(1);
  await button('清除筛选').click();
  await expect(page.locator('.attribute-section tbody tr')).toHaveCount(6);
  await screenshot('table-desktop', 1440, 900);
  await screenshot('table-narrow', 390, 844);
  await page.setViewportSize({ width: 1440, height: 900 });
  await button('导出当前数据').click();
  await task('数据导出');
  expect((await readFile(path.join(output, 'table-export.gpkg'))).length).toBeGreaterThan(1000);
  result.checks.push('CSV native import, explicit preview, table-only selection, filtering, export and draft preservation passed.');
  await button('生成点数据').click();
  dialog = page.getByRole('dialog', { name: '生成点数据' });
  await expect(dialog.getByRole('button', { name: '生成点图层' })).toBeDisabled();
  await dialog.getByLabel('X 字段').selectOption('x');
  await dialog.getByLabel('Y 字段').selectOption('y');
  await dialog.getByLabel('点数据来源 CRS').fill('EPSG:4326');
  await screenshot('point-options-narrow', 390, 844);
  await page.setViewportSize({ width: 1440, height: 900 });
  await dialog.getByRole('button', { name: '生成点图层' }).click();
  await expect(dialog).toBeHidden();
  await task('生成点数据');
  await expect(page.locator('.layer-select')).toHaveCount(1);
  await expect(page.locator('.attribute-section tbody tr')).toHaveCount(6);
  await button('定位当前图层').click();
  await expect.poll(async () => (await pixels()).colored).toBeGreaterThan(20);
  await expect.poll(async () => (await pixels()).touchesEdge).toBe(false);
  const before = await page.locator('.vector-map').screenshot();
  await button('地图放大').click();
  await expect.poll(async () => !(await page.locator('.vector-map').screenshot()).equals(before)).toBe(true);
  await button('定位当前图层').click();
  await screenshot('points-desktop', 1440, 900, true);
  await screenshot('points-narrow', 390, 844, true);
  await page.setViewportSize({ width: 1440, height: 900 });
  await button('数据与检查报告').click();
  await expect(page.getByRole('complementary', { name: '数据与检查报告' })).toBeVisible();
  await screenshot('point-report', 1440, 900);
  await button('数据与检查报告').click();
  result.checks.push('Explicit X/Y/CRS creates a selectable point layer while preserving all source rows; canvas is nonblank, framed and responds to zoom.');
  await pickers(sources[2].sourcePath, path.join(output, 'xlsx-export.gpkg'));
  await button('导入表格').click();
  dialog = page.getByRole('dialog', { name: '导入坐标表' });
  await dialog.getByRole('button', { name: '选择表格文件' }).click();
  await expect(dialog.getByLabel('工作表')).toBeEnabled();
  await expect(dialog.getByRole('button', { name: '导入表格', exact: true })).toBeDisabled();
  await dialog.getByLabel('工作表').selectOption('Coordinates');
  await dialog.getByLabel('表头行').fill('2');
  await expect(dialog.getByRole('region', { name: '表格预览' })).toContainText('=114+0.3');
  await screenshot('xlsx-preview', 1440, 900);
  await dialog.getByRole('button', { name: '导入表格', exact: true }).click();
  await expect(dialog).toBeHidden();
  await task('数据导入');
  await expect(page.locator('.table-select')).toHaveCount(2);
  await expect(page.locator('.attribute-section tbody tr')).toHaveCount(10);
  await expect(page.locator('.attribute-section')).toContainText('=114+0.3');
  await button('导出当前数据').click();
  await task('数据导出');
  await button('保存项目').click();
  await expect(page.getByText('项目已保存', { exact: true })).toBeVisible();
  await button('关闭项目').click();
  await button('打开项目').click();
  await expect(page.locator('.table-select')).toHaveCount(2);
  await expect(page.locator('.layer-select')).toHaveCount(1);
  await page.locator('.table-select').nth(1).click();
  await expect(page.locator('.attribute-section tbody tr')).toHaveCount(10);
  result.checks.push('XLSX requires worksheet/header selection; formula text remains visible; export and reopen retain both tables and derived layer.');
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
