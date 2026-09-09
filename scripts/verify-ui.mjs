import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(root, 'apps/desktop/package.json'));
const { chromium, expect } = require('@playwright/test');
const native = process.argv.includes('--native');
const endpointIndex = process.argv.indexOf('--cdp');
const endpoint = endpointIndex >= 0 ? process.argv[endpointIndex + 1] : 'http://127.0.0.1:9224';
const output = path.join(root, '.artifacts', `ui-${native ? 'native' : 'browser'}-${Date.now()}`);
await mkdir(output, { recursive: true });
const browser = native
  ? await chromium.connectOverCDP(endpoint)
  : await chromium.launch({ channel: 'msedge', headless: true });
const page = native
  ? browser.contexts()[0].pages().find((candidate) => candidate.url().includes('1420'))
  : await browser.newPage();
if (!page) throw new Error('Native WebView2 page was not found.');
const errors = [];
page.on('pageerror', (error) => errors.push(String(error)));
const results = { mode: native ? 'native' : 'browser', screenshots: [], checks: [], errors };

async function screenshot(name, width, height) {
  await page.setViewportSize({ width, height });
  await page.evaluate(() => document.fonts.ready);
  if (await page.locator('.projection-map canvas').count()) {
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect.poll(() => page.locator('.projection-map canvas').evaluate((canvas) => {
      const { width, height } = canvas;
      const data = canvas.getContext('2d').getImageData(0, 0, width, height).data;
      let colored = 0;
      let touchesEdge = false;
      for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
          if (data[(y * width + x) * 4 + 3] === 0) continue;
          colored++;
          if (x < 4 || y < 4 || x >= width - 4 || y >= height - 4) touchesEdge = true;
        }
      }
      return colored > 100 && !touchesEdge;
    })).toBe(true);
  }
  const overflow = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
  expect(overflow.document).toBeLessThanOrEqual(overflow.viewport + 1);
  await page.screenshot({ path: path.join(output, `${name}.png`), fullPage: true });
  results.screenshots.push(`${name}.png`);
}

try {
  if (!native) {
    await page.goto('http://127.0.0.1:1420/', { waitUntil: 'networkidle' });
    await expect(page.getByRole('button', { name: '新建项目', exact: true })).toBeDisabled();
    await expect(page.getByText('浏览器模式', { exact: true })).toBeVisible();
    await screenshot('desktop-empty', 1440, 900);
    await screenshot('mobile-empty', 390, 844);
    await page.getByRole('tab', { name: '投影验证' }).click();
    await expect(page.getByText('暂无投影验证结果')).toBeVisible();
    await screenshot('mobile-projection-empty', 390, 844);
    results.checks.push('Browser mode does not pretend native operations are available.', 'Desktop/mobile layouts have no horizontal overflow.');
  } else {
    await page.reload({ waitUntil: 'networkidle' });
    await expect(page.getByText('引擎已连接', { exact: true })).toBeVisible({ timeout: 90000 });
    const parent = path.join(output, 'projects');
    const projectName = '用地验证项目';
    const projectPath = path.join(parent, projectName, 'project.spa');
    await mkdir(parent, { recursive: true });
    // Replace only the OS file picker for automation; all engine IPC remains real.
    await page.evaluate(async ({ parent, projectPath }) => {
      const { desktop } = await import('/src/bridge.ts');
      desktop.chooseParent = () => Promise.resolve(parent);
      desktop.chooseProject = () => Promise.resolve(projectPath);
    }, { parent, projectPath });
    await page.getByRole('button', { name: '新建项目', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('项目名称').fill(projectName);
    await dialog.getByRole('button', { name: '选择父目录' }).click();
    await dialog.getByRole('button', { name: '创建项目', exact: true }).click();
    await expect(page.getByLabel('项目描述')).toBeVisible();
    await page.getByLabel('项目描述').fill('原生桥接保存与重开验证');
    await page.getByLabel('分析 CRS', { exact: true }).fill('EPSG:4547');
    await page.getByRole('button', { name: '保存项目', exact: true }).click();
    await expect(page.getByRole('button', { name: '保存项目', exact: true })).toBeDisabled();
    await page.getByRole('button', { name: '运行验证', exact: true }).click();
    await expect(page.getByText('全部通过', { exact: true })).toBeVisible({ timeout: 90000 });
    await screenshot('desktop-diagnostics', 1440, 900);
    await page.getByRole('tab', { name: '投影验证' }).click();
    await expect(page.locator('.projection-map canvas')).toBeVisible();
    const pixelCount = () => page.locator('.projection-map canvas').evaluate((canvas) => {
      const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      let colored = 0;
      for (let index = 3; index < data.length; index += 4) if (data[index] > 0) colored++;
      return colored;
    });
    await expect.poll(pixelCount).toBeGreaterThan(1000);
    const beforeZoom = await pixelCount();
    await page.getByRole('button', { name: '放大', exact: true }).click();
    await expect.poll(pixelCount).not.toBe(beforeZoom);
    await page.getByRole('button', { name: '缩放至诊断几何' }).click();
    await screenshot('desktop-projection', 1440, 900);
    await screenshot('mobile-projection', 390, 844);
    await expect.poll(pixelCount).toBeGreaterThan(100);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.getByRole('button', { name: '关闭项目', exact: true }).click();
    await page.getByRole('button', { name: '打开项目', exact: true }).click();
    await expect(page.getByLabel('项目描述')).toHaveValue('原生桥接保存与重开验证');
    await expect(page.getByLabel('分析 CRS', { exact: true })).toHaveValue('EPSG:4547');
    await page.getByLabel('项目描述').fill('未保存草稿');
    await page.getByRole('button', { name: '关闭项目', exact: true }).click();
    await expect(page.getByRole('dialog', { name: '项目有未保存的更改' })).toBeVisible();
    await page.getByRole('dialog').locator('.modal-actions').getByRole('button', { name: '取消', exact: true }).click();
    await expect(page.getByLabel('项目描述')).toHaveValue('未保存草稿');
    await page.getByLabel('项目描述').fill('原生桥接保存与重开验证');
    results.checks.push('Native create/save/close/reopen preserved project fields.', 'Genuine GIS diagnostic checks passed.', 'OpenLayers canvas contains geometry and zoom changes its pixels.', 'Desktop/mobile geometry fits inside canvas without clipping.', 'Desktop/mobile have no horizontal overflow.', 'Canceling the unsaved dialog preserved the draft.');
    results.projectPath = projectPath;
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
