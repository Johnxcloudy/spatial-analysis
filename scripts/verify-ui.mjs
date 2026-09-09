import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(root, 'apps/desktop/package.json'));
const { chromium, expect } = require('@playwright/test');
const native = process.argv.includes('--native');
const arg = (name, fallback) => { const i = process.argv.indexOf(name); return i < 0 ? fallback : process.argv[i + 1]; };
const output = path.join(root, '.artifacts', `ui-${native ? 'native' : 'browser'}-${Date.now()}`);
await mkdir(output, { recursive: true });
const browser = native ? await chromium.connectOverCDP(arg('--cdp', 'http://127.0.0.1:9224')) : await chromium.launch({ channel: 'msedge', headless: true });
const page = native ? browser.contexts()[0].pages().find((candidate) => candidate.url().includes('1420')) : await browser.newPage();
if (!page) throw new Error('Native development WebView2 page was not found.');
page.setDefaultTimeout(15000);
const errors = [];
page.on('pageerror', (error) => errors.push(String(error)));
const results = { mode: native ? 'native' : 'browser', screenshots: [], checks: [], errors };
const button = (name) => page.getByRole('button', { name, exact: true });
const tab = (name) => page.getByRole('tab', { name, exact: true });

async function pixels(selector = '.vector-map canvas') {
  return page.locator(selector).evaluateAll((canvases) => {
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
      return stable >= 3 && current.colored > 100 && !current.touchesEdge
        && !await page.locator('.map-loading').isVisible();
    }, { timeout: 15000, intervals: [150] }).toBe(true);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  const screenshotPath = path.join(output, `${name}.png`);
  if (native) {
    // WebView2 fullPage can subtract its scrollbar twice under viewport emulation.
    const session = await page.context().newCDPSession(page);
    const contentHeight = await page.evaluate(() => Math.max(innerHeight, document.documentElement.scrollHeight));
    const capture = await session.send('Page.captureScreenshot', {
      format: 'png', captureBeyondViewport: true,
      clip: { x: 0, y: 0, width, height: contentHeight, scale: 1 },
    });
    await writeFile(screenshotPath, Buffer.from(capture.data, 'base64'));
    await session.detach();
  } else {
    await page.screenshot({ path: screenshotPath, fullPage: true });
  }
  results.screenshots.push(`${name}.png`);
}

async function waitForTask(kind) {
  await expect(page.locator('.task-strip').getByText(kind, { exact: true })).toBeVisible();
  await expect(page.locator('.task-strip .task-stage')).toHaveText('已完成', { timeout: 90000 });
  await expect(button('导入数据')).toBeEnabled();
}

async function chooseImport(layer, gdb = false) {
  await button('导入数据').click();
  const dialog = page.getByRole('dialog', { name: '导入矢量数据' });
  await dialog.getByRole('button', { name: gdb ? '选择 GDB' : '选择文件', exact: true }).click();
  await dialog.getByLabel('源数据层').selectOption(layer);
  await dialog.getByRole('button', { name: '导入所选数据层', exact: true }).click();
  await expect(dialog).toBeHidden();
  await waitForTask('数据导入');
}

try {
  if (!native) {
    await page.goto('http://127.0.0.1:1420/', { waitUntil: 'networkidle' });
    await expect(button('新建项目')).toBeDisabled();
    await expect(page.getByText('浏览器模式', { exact: true })).toBeVisible();
    await screenshot('desktop-empty', 1440, 900);
    await screenshot('mobile-empty', 390, 844);
    await tab('投影验证').click();
    await expect(page.getByText('暂无投影验证结果')).toBeVisible();
    await screenshot('mobile-projection-empty', 390, 844);
    results.checks.push('Browser mode disables native operations; desktop/narrow layouts have no document overflow.');
  } else {
    const fixtures = JSON.parse(await readFile(arg('--fixtures', path.join(root, '.artifacts/vector-fixtures-phase1a/manifest.json')), 'utf8'));
    await page.reload({ waitUntil: 'networkidle' });
    await expect(page.getByText('引擎已连接', { exact: true })).toBeVisible({ timeout: 90000 });
    await page.setViewportSize({ width: 1440, height: 900 });
    const parent = path.join(output, 'projects');
    const projectName = '用地验证项目';
    const projectPath = path.join(parent, projectName, 'project.spa');
    const exportPath = path.join(output, 'native-export.gpkg');
    await mkdir(parent, { recursive: true });
    // Replace only OS file pickers. Every data operation still uses the real native service.
    await page.evaluate(async ({ parent, projectPath, exportPath, fixtures }) => {
      const addresses = performance.getEntriesByType('resource').map((entry) => entry.name)
        .filter((name) => new URL(name).pathname === '/src/bridge.ts');
      for (const address of new Set(addresses.length ? addresses : ['/src/bridge.ts'])) {
        const { desktop } = await import(address);
        desktop.chooseParent = () => Promise.resolve(parent);
        desktop.chooseProject = () => Promise.resolve(projectPath);
        desktop.chooseVector = () => Promise.resolve(fixtures.gpkg);
        desktop.chooseGdb = () => Promise.resolve(fixtures.gdb);
        desktop.chooseExport = () => Promise.resolve(exportPath);
      }
    }, { parent, projectPath, exportPath, fixtures });
    await button('新建项目').click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('项目名称').fill(projectName);
    await dialog.getByRole('button', { name: '选择父目录', exact: true }).click();
    await dialog.getByRole('button', { name: '创建项目', exact: true }).click();
    await expect(dialog).toBeHidden();
    await tab('项目').click();
    await page.getByLabel('项目描述', { exact: true }).fill('导入期间保留的未保存草稿');
    await page.getByLabel('分析 CRS', { exact: true }).fill('EPSG:4547');
    await chooseImport('land');
    await expect(page.getByLabel('项目描述')).toHaveValue('导入期间保留的未保存草稿');
    await tab('图层').click();
    await expect(page.locator('.attribute-section tbody tr')).toHaveCount(4);
    await expect(page.locator('.attribute-section')).toContainText('9007199254740993');
    await button('缩放至图层').click();
    await expect.poll(async () => (await pixels()).colored).toBeGreaterThan(500);
    const beforeZoom = (await pixels()).colored;
    await button('地图放大').click();
    await expect.poll(async () => (await pixels()).colored).not.toBe(beforeZoom);
    await button('定位当前图层').click();
    await button('隐藏 land').click();
    await expect.poll(async () => (await pixels()).colored).toBe(0);
    await button('显示 land').click();
    await expect.poll(async () => (await pixels()).colored).toBeGreaterThan(500);
    await page.getByLabel('分类渲染字段').selectOption('code');
    await expect(page.getByLabel('分类渲染字段')).toBeEnabled();
    await page.getByLabel('类别值', { exact: true }).fill('001');
    await page.getByLabel('类别颜色', { exact: true }).fill('#008577');
    await button('设置').click();
    await expect(page.getByLabel('类别 001 颜色', { exact: true })).toHaveValue('#008577');
    await page.getByLabel('筛选字段').selectOption('code');
    await page.getByLabel('筛选条件').selectOption('equals');
    await page.getByLabel('筛选值').fill('002');
    await button('应用筛选').click();
    await expect(page.locator('.attribute-section tbody tr')).toHaveCount(1);
    await expect(page.locator('.attribute-section tbody')).toContainText('002');
    await button('清除筛选').click();
    await expect(page.locator('.attribute-section tbody tr')).toHaveCount(4);
    await page.locator('.attribute-section tbody tr').nth(1).click();
    await expect(page.locator('.selected-record')).toContainText('选中 ID：2');
    await expect(page.locator('.attribute-section tbody tr').nth(1)).toHaveAttribute('aria-selected', 'true');
    await button('清除要素选择').click();
    await button('定位当前图层').click();
    await expect.poll(async () => (await pixels()).touchesEdge).toBe(false);
    const point = await page.locator('.vector-map canvas').evaluateAll((canvases) => {
      for (const canvas of canvases) {
        const { width, height } = canvas;
        const rect = canvas.getBoundingClientRect();
        const data = canvas.getContext('2d').getImageData(0, 0, width, height).data;
        for (let y = 10; y < height - 10; y++) for (let x = 10; x < width - 10; x++) {
          if (data[(y * width + x) * 4 + 3] > 50) return { x: rect.left + x * rect.width / width, y: rect.top + y * rect.height / height };
        }
      }
      return null;
    });
    expect(point).not.toBeNull();
    await page.mouse.click(point.x, point.y);
    await expect(page.locator('.selected-record')).toContainText('选中 ID：');
    await button('清除要素选择').click();
    await button('数据与检查报告').click();
    await screenshot('desktop-data-report', 1440, 900, true);
    await button('数据与检查报告').click();
    await screenshot('desktop-map', 1440, 900, true);
    await screenshot('mobile-map', 390, 844, true);
    await page.setViewportSize({ width: 1440, height: 900 });
    await button('导出当前数据').click();
    await waitForTask('数据导出');
    expect((await readFile(exportPath)).length).toBeGreaterThan(1000);
    await chooseImport('controls', true);
    await expect(page.locator('.layer-row')).toHaveCount(2);
    await button('上移 controls').click();
    await expect(page.locator('.layer-row').first()).toContainText('controls');
    await button('保存项目').click();
    await expect(button('保存项目')).toBeDisabled();
    await button('关闭项目').click();
    await button('打开项目').click();
    await expect(page.locator('.layer-row')).toHaveCount(2);
    await expect(page.locator('.layer-row').first()).toContainText('controls');
    await tab('项目').click();
    await expect(page.getByLabel('项目描述')).toHaveValue('导入期间保留的未保存草稿');
    await page.getByLabel('项目描述').fill('未保存草稿');
    await button('关闭项目').click();
    await page.getByRole('dialog', { name: '项目有未保存的更改' }).locator('.modal-actions').getByRole('button', { name: '取消', exact: true }).click();
    await expect(page.getByLabel('项目描述')).toHaveValue('未保存草稿');
    await button('保存项目').click();
    await tab('运行诊断').click();
    await button('运行验证').click();
    await expect(page.getByText('全部通过', { exact: true })).toBeVisible({ timeout: 90000 });
    await tab('投影验证').click();
    await expect.poll(async () => (await pixels('.projection-map canvas')).colored).toBeGreaterThan(100);
    await screenshot('desktop-projection', 1440, 900);
    results.checks.push('Real native GPKG import and GDB class selection; drafts preserved.', 'Map geometry is nonblank, zoom and visibility work; desktop/narrow fitting is checked.', 'Category styles, filters and map/table selection work.', 'GPKG export, layer order, project reopen and unsaved protection work.', 'GIS diagnostics still pass.');
    results.projectPath = projectPath;
    results.exportPath = exportPath;
  }
  expect(errors).toEqual([]);
  results.ok = true;
} catch (error) {
  results.ok = false;
  results.failure = String(error);
  await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {});
  throw error;
} finally {
  await writeFile(path.join(output, 'verification.json'), JSON.stringify(results, null, 2));
  if (!native) await browser.close();
  console.log(JSON.stringify({ output, ...results }, null, 2));
  if (native) process.exit(results.ok ? 0 : 1);
}
