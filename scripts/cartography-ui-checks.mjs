// Native acceptance extension; imported by verify-pagination-ui.mjs --cartography.
import { createReadStream } from 'node:fs';
import { createHash } from 'node:crypto';
import path from 'node:path';

async function digest(file) {
  const hash = createHash('sha256');
  for await (const bytes of createReadStream(file)) hash.update(bytes);
  return hash.digest('hex');
}

/** Runs inside the WebView; reads map canvases only, never legend SVGs. */
export function mapColorEvidence(colors) {
  const targets = colors.map((color) => ({ color, rgb: color.slice(1).match(/../g).map((part) => parseInt(part, 16)), pixels: 0 }));
  let readableCanvases = 0;
  for (const canvas of document.querySelectorAll('.map-target canvas')) {
    const rectangle = canvas.getBoundingClientRect();
    if (!rectangle.width || !rectangle.height || !canvas.width || !canvas.height) continue;
    let visible = true;
    for (let element = canvas; element; element = element.parentElement) {
      const style = getComputedStyle(element);
      if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) { visible = false; break; }
    }
    if (!visible) continue;
    const context = canvas.getContext('2d');
    if (!context) continue;
    const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
    readableCanvases++;
    for (let offset = 0; offset < data.length; offset += 4) {
      // Opaque interior pixels avoid attributing antialiasing or translucent overlays to a fill.
      if (data[offset + 3] !== 255) continue;
      for (const target of targets) if (data[offset] === target.rgb[0] && data[offset + 1] === target.rgb[1] && data[offset + 2] === target.rgb[2]) target.pixels++;
    }
  }
  return { readableCanvases, colors: targets.map(({ color, pixels }) => ({ color, pixels })) };
}

export async function verifyCartographyUi({ page, expect, rpc, open, close, selectDataset, screenshot, phase, cases, report }) {
  const button = (name) => page.getByRole('button', { name, exact: true });
  const legend = () => page.locator('.vector-legend');
  const currentLayer = async (project, id) => (await rpc('workspace.get', { path: project })).layers.find((layer) => layer.id === id);
  const settle = async () => {
    await expect(page.locator('.map-loading')).toHaveCount(0);
    await page.waitForTimeout(350); // Let the existing 180ms viewport debounce settle.
    await expect(page.locator('.map-loading')).toHaveCount(0);
  };
  const viewportCount = () => page.evaluate(() => window.__paginationTrace.events.filter((item) => item.method === 'vector.viewport').length);
  const requireMapColors = async (colors) => {
    let evidence;
    await expect.poll(async () => {
      evidence = await page.evaluate(mapColorEvidence, colors);
      return evidence.readableCanvases > 0 && evidence.colors.every((entry) => entry.pixels >= 8);
    }, { message: `Expected opaque input-map fill pixels for ${colors.join(', ')}`, timeout: 10000 }).toBe(true);
    return evidence;
  };
  const checks = [];
  report.cartography = { ok: false, checks, scope: 'Manual configured symbols on copied synthetic 100k/250k/500k projects. No class discovery, new full analysis, formal export or OS cold-cache claim.' };
  for (const item of cases) {
    await close();
    const phaseName = `cartography-${item.features}`;
    await phase(phaseName);
    const workspace = await open(item.projectPath);
    const dataset = workspace.datasets.find((entry) => entry.id === item.inputDatasetId);
    const original = workspace.layers.find((entry) => entry.datasetId === dataset.id);
    expect(original).toBeTruthy();
    const orderedLayers = [...workspace.layers].sort((a, b) => a.order - b.order);
    const setVisibility = async (definition, visible) => {
      const eye = page.locator('.layer-eye').nth(orderedLayers.findIndex((entry) => entry.id === definition.id));
      const expected = `${visible ? '隐藏' : '显示'} ${definition.name}`;
      if (await eye.getAttribute('aria-label') !== expected) await eye.click();
      await expect(eye).toHaveAttribute('aria-label', expected);
    };
    const waitForRevision = async (revision) => {
      await expect.poll(async () => (await currentLayer(item.projectPath, original.id)).cartographyRevision,
        { message: 'Wait for the requested cartography revision to persist', timeout: 10000 }).toBe(revision);
      await expect(button('采用 Planning 预设')).toBeEnabled();
      return currentLayer(item.projectPath, original.id);
    };
    const identities = await Promise.all(workspace.datasets.map(async (entry) => ({ path: entry.relativePath, sha256: await digest(path.join(path.dirname(item.projectPath), entry.relativePath)) })));
    await selectDataset(workspace, dataset.id);
    for (const definition of orderedLayers) await setVisibility(definition, definition.id === original.id);
    await button('定位当前图层').click();
    await page.waitForTimeout(250);
    // Fitting 500k tiny parcels makes their fill narrower than an outline. Inspect a
    // bounded central viewport at readable scale, still using the full-size snapshot.
    for (let zoom = 0; zoom < 5; zoom++) {
      await button('地图放大').click();
      await page.waitForTimeout(200);
    }
    await settle();
    await expect.poll(() => page.evaluate(({ datasetId, phaseName }) => {
      const trace = window.__paginationTrace;
      const alias = trace.aliases.get(datasetId);
      return trace.events.some((event) => event.method === 'vector.viewport' && event.datasetAlias === alias && event.phase === phaseName && event.completionAvailable && event.responseHeader === 'ok');
    }, { datasetId: dataset.id, phaseName }), { message: 'Expected a successful observed input viewport response; dispatch-only transport is insufficient', timeout: 10000 }).toBe(true);
    await expect(page.locator('.map-target canvas').first()).toBeVisible();
    await page.evaluate(() => {
      const state = { stalls: [], last: performance.now() };
      state.timer = setInterval(() => { const now = performance.now(); state.stalls.push(Math.max(0, now - state.last - 25)); state.last = now; }, 25);
      window.__cartographyTimer = state;
    });
    try {
      const before = await viewportCount();
      await button('采用 Planning 预设').click();
      expect((await currentLayer(item.projectPath, original.id)).cartography ?? null).toBeNull();
      await page.getByLabel('图例标题', { exact: true }).fill(`Configured symbols ${item.features}`);
      await button('应用专题样式').click();
      let adopted = await waitForRevision((original.cartographyRevision ?? 0) + 1);
      expect(adopted.cartography.basePreset).toBe('planning');
      await expect(legend()).toBeVisible();
      await expect(legend()).toContainText('不表示类别存在或完整覆盖');
      const initialMapPixels = await requireMapColors([adopted.cartography.symbol.fillColor]);
      await settle();
      expect(await viewportCount()).toBe(before);

      const colors = ['#127f73', '#bf7438'];
      const applyMs = [];
      const mapPixels = [];
      for (const color of colors) {
        const count = await viewportCount();
        const previousColor = adopted.cartography.symbol.fillColor;
        const beforePixels = await page.evaluate(mapColorEvidence, [previousColor, color]);
        expect(beforePixels.colors[0].pixels).toBeGreaterThanOrEqual(8);
        expect(beforePixels.colors[1].pixels).toBe(0);
        const started = performance.now();
        await page.getByLabel('专题填色', { exact: true }).fill(color);
        await button('应用专题样式').click();
        adopted = await waitForRevision(adopted.cartographyRevision + 1);
        expect(adopted.cartography.symbol.fillColor).toBe(color);
        await expect(legend().locator('svg rect')).toHaveAttribute('fill', color);
        const afterPixels = await requireMapColors([color]);
        const retiredPixels = await page.evaluate(mapColorEvidence, [previousColor]);
        expect(retiredPixels.colors[0].pixels).toBe(0);
        mapPixels.push({ previousColor, color, before: beforePixels, after: afterPixels, retired: retiredPixels });
        applyMs.push(performance.now() - started);
        await settle();
        expect(await viewportCount()).toBe(count);
      }
      await button('采用 Publication 预设').click();
      await button('应用专题样式').click();
      adopted = await waitForRevision(adopted.cartographyRevision + 1);
      expect(adopted.cartography.basePreset).toBe('publication');
      const publicationMapPixels = await requireMapColors([adopted.cartography.symbol.fillColor]);

      await page.getByLabel('专题渲染', { exact: true }).selectOption('code');
      for (const entry of [
        { value: '001', label: 'Leading zeros', color: '#26856b' },
        { value: '', label: 'Empty string', color: '#c57136' },
        { value: 'null', label: 'Literal null', color: '#7956a5' },
        { value: 'constructor', label: 'Literal constructor', color: '#3d81b8' },
      ]) {
        await page.getByLabel('新类别类型', { exact: true }).selectOption('string');
        await page.getByLabel('新类别值', { exact: true }).fill(entry.value);
        await page.getByLabel('新类别标签', { exact: true }).fill(entry.label);
        await page.getByLabel('新类别颜色', { exact: true }).fill(entry.color);
        await button('添加类别').click();
      }
      await button('应用专题样式').click();
      adopted = await waitForRevision(adopted.cartographyRevision + 1);
      expect(adopted.cartography.renderer.categories.map((entry) => entry.value)).toEqual(['001', '', 'null', 'constructor']);
      await expect(legend().locator('li')).toHaveCount(6);
      await expect(legend()).toContainText('Literal null');
      await expect(legend()).toContainText('NULL');
      await expect(legend()).toContainText('其他');
      await settle();
      const categorizedMapPixels = await requireMapColors(['#26856b', '#c57136', adopted.cartography.renderer.nullColor, adopted.cartography.renderer.otherColor]);
      await screenshot(`cartography-${item.features}-configured-legend`);
      const saved = adopted.cartography;
      await close();
      const reopened = await open(item.projectPath);
      expect(reopened.layers.find((entry) => entry.id === original.id).cartography).toEqual(saved);
      await selectDataset(reopened, dataset.id);
      await expect(legend().locator('li')).toHaveCount(6);
      const reopenedMapPixels = await requireMapColors(['#26856b', '#c57136', saved.renderer.nullColor, saved.renderer.otherColor]);
      await button('恢复原有样式').click();
      const restored = await waitForRevision(saved.revision + 1);
      expect(restored.cartography).toBeNull();
      expect(restored.cartographyRevision).toBe(saved.revision + 1);
      for (const key of ['color', 'categoryField', 'categoryColors']) expect(restored[key]).toEqual(original[key]);
      await expect(legend()).toHaveCount(0);
      for (const definition of orderedLayers) await setVisibility(definition, definition.visible);
      const after = await rpc('workspace.get', { path: item.projectPath });
      expect(after.datasets).toEqual(workspace.datasets);
      for (const identity of identities) expect(await digest(path.join(path.dirname(item.projectPath), identity.path))).toBe(identity.sha256);
      const timing = await page.evaluate(() => { const state = window.__cartographyTimer; clearInterval(state.timer); return { samples: state.stalls.length, maxStallMs: Math.max(0, ...state.stalls) }; });
      expect(timing.samples).toBeGreaterThan(20);
      expect(timing.maxStallMs).toBeLessThanOrEqual(500);
      checks.push({ features: item.features, ok: true, snapshotsUnchanged: identities.length, colorChangesDoNotRefetchGeometry: true, categoryField: 'code', manuallyConfiguredCategories: 4, restoredRevision: restored.cartographyRevision,
        initialMapPixels, mapPixels, publicationMapPixels, categorizedMapPixels, reopenedMapPixels,
        mapEvidenceScope: 'Only the input layer visible, fitted then zoomed five steps; exact opaque interior RGB pixels from map-target canvases, excluding legend SVGs. Current-phase input viewport fetch success required. Existing display budgets apply.',
        timing, applyObservedMs: applyMs, applyTimingScope: 'Playwright input/apply plus native workspace and canvas verification; includes automation overhead.' });
    } finally {
      await page.evaluate(() => { if (window.__cartographyTimer) clearInterval(window.__cartographyTimer.timer); });
    }
  }
  report.cartography.ok = true;
}
