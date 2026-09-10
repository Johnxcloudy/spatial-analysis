import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const probeRoot = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(probeRoot, '../..');
const require = createRequire(path.join(root, 'apps/desktop/package.json'));
const { chromium } = require('@playwright/test');
const { createServer } = await import(pathToFileURL(require.resolve('vite')).href);
const arg = (name) => { const index = process.argv.indexOf(name); if (index < 0 || !process.argv[index + 1]) throw new Error(`${name} required`); return process.argv[index + 1]; };
const fixture = path.resolve(arg('--fixture-dir'));
const output = path.resolve(arg('--output'));
const fontPath = path.resolve(arg('--font'));
await mkdir(output, { recursive: false });
let browser, server;
const errors = [];
const result = { ok: false, errors, viewports: [], identityVerified: false, browserStarted: false };
const digest = (bytes) => createHash('sha256').update(bytes).digest('hex');
const sameIdentity = (name, actual, expected) => {
  if (typeof expected !== 'string' || actual !== expected) throw new Error(`Asset identity mismatch: ${name}`);
};
try {
  const report = JSON.parse(await readFile(path.join(fixture, 'python-report.json'), 'utf8'));
  if (!report.ok) throw new Error('Python full-data fixture verification must pass first');
  const staticFiles = {
    '/fixture.json': { path: path.join(fixture, 'scene.json'), type: 'application/json', expected: report.output.artifacts['scene.json'].sha256 },
    '/raster.png': { path: path.join(fixture, 'raster.png'), type: 'image/png', expected: report.output.artifacts['raster.png'].sha256 },
    '/font.ttf': { path: fontPath, type: 'font/ttf', expected: report.font.sha256 },
  };
  // Hash once and serve those exact buffers so a later filesystem edit cannot change the render inputs.
  result.servedAssets = {};
  for (const [route, asset] of Object.entries(staticFiles)) {
    asset.bytes = await readFile(asset.path);
    result.servedAssets[route] = { sha256: digest(asset.bytes), bytes: asset.bytes.length, requests: 0 };
    sameIdentity(route, result.servedAssets[route].sha256, asset.expected);
  }
  result.fontSha256 = result.servedAssets['/font.ttf'].sha256;
  result.inputSha256 = {};
  for (const name of ['land.gpkg', 'background.tif']) {
    result.inputSha256[name] = digest(await readFile(path.join(fixture, name)));
    sameIdentity(name, result.inputSha256[name], report.inputSha256[name]);
  }
  result.identityVerified = true;
  server = await createServer({ configFile: false, root: probeRoot, logLevel: 'error',
  resolve: { alias: { ol: path.join(root, 'apps/desktop/node_modules/ol') } },
  server: { host: '127.0.0.1', port: 0, strictPort: false, fs: { allow: [root] } },
  plugins: [{ name: 'bounded-probe-files', configureServer(instance) {
    instance.middlewares.use((request, response, next) => {
      const file = staticFiles[request.url];
      if (!file) return next();
      result.servedAssets[request.url].requests += 1;
      response.setHeader('Content-Type', file.type);
      response.end(file.bytes);
    });
  } }],
  });
  await server.listen();
  const port = server.httpServer.address().port;
  browser = await chromium.launch({ channel: 'msedge', headless: true });
  result.browserStarted = true;
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 }, deviceScaleFactor: 1 });
  page.on('pageerror', (error) => errors.push(String(error)));
  await page.goto(`http://127.0.0.1:${port}/`);
  await page.waitForFunction(() => window.probe?.ok, null, { timeout: 60000 });
  const { dataUrl, renderedFeatureIds, ...evidence } = await page.evaluate(() => window.probe);
  if (renderedFeatureIds.length !== 2103 || renderedFeatureIds[2102] !== 2103 || new Set(renderedFeatureIds).size !== 2103)
    throw new Error('Incomplete or duplicate feature identity population');
  await writeFile(path.join(output, 'openlayers.png'), Buffer.from(dataUrl.split(',')[1], 'base64'));
  for (const [name, viewport] of [['desktop', { width: 1400, height: 1000 }], ['mobile', { width: 390, height: 844 }]]) {
    await page.setViewportSize(viewport);
    const layout = await page.evaluate(() => {
      const image = document.getElementById('preview');
      const rect = image.getBoundingClientRect();
      const canvas = document.createElement('canvas');
      canvas.width = 200; canvas.height = 142;
      const context = canvas.getContext('2d');
      context.drawImage(image, 0, 0, 200, 142);
      const pixels = context.getImageData(0, 0, 200, 142).data;
      let colored = 0;
      for (let index = 0; index < pixels.length; index += 4) if (pixels[index] !== pixels[index + 1] || pixels[index + 1] !== pixels[index + 2]) colored++;
      return { width: rect.width, height: rect.height, left: rect.left, right: rect.right, naturalWidth: image.naturalWidth,
        scrollWidth: document.documentElement.scrollWidth, viewport: innerWidth, complete: image.complete, colored };
    });
    if (!layout.complete || layout.naturalWidth !== 3508 || layout.left < 0 || layout.right > viewport.width || layout.scrollWidth > viewport.width || layout.colored < 1000)
      throw new Error(`Preview layout/pixel failure: ${JSON.stringify(layout)}`);
    await page.screenshot({ path: path.join(output, `${name}.png`), fullPage: true });
    result.viewports.push({ name, ...layout });
  }
  if (errors.length) throw new Error('Browser errors occurred');
  if (Object.values(result.servedAssets).some((asset) => asset.requests < 1)) throw new Error('A verified asset was not served');
  Object.assign(result, evidence, { ok: true, browser: browser.version(), fullInputIdentityCount: renderedFeatureIds.length,
    versions: { ol: require('ol/package.json').version, playwright: require('@playwright/test/package.json').version } });
} catch (error) {
  result.failure = String(error);
  throw error;
} finally {
  await writeFile(path.join(output, 'openlayers-report.json'), JSON.stringify(result, null, 2));
  await browser?.close();
  await server?.close();
}
console.log(JSON.stringify({ ok: result.ok, report: path.join(output, 'openlayers-report.json') }));
