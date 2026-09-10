import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { appendFile, cp, mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const arg = (name) => { const index = process.argv.indexOf(name); if (index < 0 || !process.argv[index + 1]) throw new Error(`${name} required`); return process.argv[index + 1]; };
const fixture = path.resolve(arg('--fixture-dir'));
const output = path.resolve(arg('--output'));
const font = path.resolve(arg('--font'));
const differentFont = path.resolve(arg('--different-font'));
const runner = path.join(path.dirname(fileURLToPath(import.meta.url)), 'verify-openlayers.mjs');
const digest = async (file) => createHash('sha256').update(await readFile(file)).digest('hex');
const originals = ['land.gpkg', 'background.tif', 'scene.json', 'raster.png', 'python-report.json'];
const originalHashes = await Promise.all(originals.map((name) => digest(path.join(fixture, name))));
if (await digest(font) === await digest(differentFont)) throw new Error('Negative font must contain different bytes');
await mkdir(output);
const result = { ok: false, checks: [] };
try {
  for (const [name, expected] of [['changed-font', '/font.ttf'], ['changed-scene', '/fixture.json'],
    ['changed-raster', '/raster.png'], ['changed-source', 'land.gpkg']]) {
    const selected = name === 'changed-font' ? fixture : path.join(output, `${name}-fixture`);
    if (selected !== fixture) await cp(fixture, selected, { recursive: true, errorOnExist: true, force: false });
    if (name === 'changed-scene') {
      const file = path.join(selected, 'scene.json');
      const scene = JSON.parse(await readFile(file, 'utf8'));
      scene.title = 'Changed title with otherwise identical geometry';
      await writeFile(file, JSON.stringify(scene));
    }
    if (name === 'changed-raster') await writeFile(path.join(selected, 'raster.png'), await readFile(path.join(selected, 'python.png')));
    if (name === 'changed-source') await appendFile(path.join(selected, 'land.gpkg'), 'changed snapshot bytes');
    const runOutput = path.join(output, `${name}-output`);
    const run = spawnSync(process.execPath, [runner, '--fixture-dir', selected, '--output', runOutput,
      '--font', name === 'changed-font' ? differentFont : font], { encoding: 'utf8', timeout: 30000, windowsHide: true });
    const report = JSON.parse(await readFile(path.join(runOutput, 'openlayers-report.json'), 'utf8'));
    if (run.status === 0 || report.ok || report.identityVerified || report.browserStarted || !report.failure.includes(`Asset identity mismatch: ${expected}`))
      throw new Error(`Identity mutation was not rejected before browser start: ${name}, ${JSON.stringify(report)}`);
    result.checks.push({ name, exitCode: run.status, ok: report.ok, identityVerified: report.identityVerified,
      browserStarted: report.browserStarted, failure: report.failure, report: path.join(runOutput, 'openlayers-report.json') });
  }
  const after = await Promise.all(originals.map((name) => digest(path.join(fixture, name))));
  if (JSON.stringify(originalHashes) !== JSON.stringify(after)) throw new Error('Negative checks modified original fixtures');
  result.ok = true;
  result.originalFixturesUnchanged = true;
} finally {
  await writeFile(path.join(output, 'negative-report.json'), JSON.stringify(result, null, 2));
}
console.log(JSON.stringify({ ok: result.ok, checks: result.checks.length, report: path.join(output, 'negative-report.json') }));
