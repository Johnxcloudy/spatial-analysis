import { describe, expect, it } from 'vitest';
import Feature from 'ol/Feature';
import Point from 'ol/geom/Point';
import LineString from 'ol/geom/LineString';
import CircleStyle from 'ol/style/Circle';
import type { MapLayer, VectorCartographySpec, VectorDataset } from '../../../shared/contracts';
import { activeCategoryField, categoryColor, cartographyValidationError, compileVectorStyle, createVectorStyleFunction, makeCartographySpec, vectorLegendEntries } from './vector-style';

const dataset = { id: 'land', version: 'sha-land', fields: [{ name: 'code' }] } as VectorDataset;
const layer: MapLayer = { id: 'layer', datasetId: 'land', name: '土地', visible: true, opacity: 1, color: '#123456', categoryField: 'legacy', categoryColors: {}, order: 0 };
function categorized(): VectorCartographySpec {
  return { ...makeCartographySpec(dataset, 'planning', '土地', 1), renderer: { kind: 'categorized', field: 'code', categories: [
    { value: 'null', label: '文字 null', color: '#100001' }, { value: '', label: '空文字', color: '#100002' },
    { value: 0, label: '数值零', color: '#100003' }, { value: '0', label: '文字零', color: '#100004' },
    { value: false, label: '布尔假', color: '#100005' }, { value: 'false', label: '文字 false', color: '#100006' },
    { value: '001', label: '前导零', color: '#100007' }, { value: 'constructor', label: '原型名称', color: '#100008' },
  ], nullColor: '#aaaaaa', nullLabel: 'NULL', otherColor: '#bbbbbb', otherLabel: '其他' } };
}

describe('vector display symbols', () => {
  it('never interprets inherited object properties as legacy colors', () => {
    expect(categoryColor(layer, 'constructor')).toMatch(/^#[0-9a-f]{6}$/i);
    expect(categoryColor(layer, 'toString')).toMatch(/^#[0-9a-f]{6}$/i);
    expect(categoryColor({ ...layer, categoryColors: { constructor: '#112233' } }, 'constructor')).toBe('#112233');
  });
  it('keeps typed categories, NULL, empty and unmatched values distinct', () => {
    const spec = categorized();
    const compiled = compileVectorStyle({ ...layer, cartography: spec });
    expect(compiled.field).toBe('code');
    if (spec.renderer.kind !== 'categorized') throw new Error('fixture');
    for (const entry of spec.renderer.categories) expect(compiled.resolve(entry.value).fillColor).toBe(entry.color);
    expect(compiled.resolve(null).fillColor).toBe('#aaaaaa');
    expect(compiled.resolve(undefined).fillColor).toBe('#bbbbbb');
    expect(compiled.resolve('unconfigured').fillColor).toBe('#bbbbbb');
    const entries = vectorLegendEntries(spec);
    expect(entries.map((entry) => entry.label)).toEqual([...spec.renderer.categories.map((item) => item.label), 'NULL', '其他']);
    expect(entries.map((entry) => entry.symbol.fillColor)).toEqual([...spec.renderer.categories.map((item) => item.color), '#aaaaaa', '#bbbbbb']);
  });
  it('uses saved symbol sizes and cached OpenLayers styles for points and category lines', () => {
    const spec = categorized();
    spec.symbol.strokeWidthPt = 3;
    spec.symbol.pointRadiusPt = 6;
    const style = createVectorStyleFunction({ ...layer, cartography: spec });
    const point = new Feature({ code: 0, geometry: new Point([0, 0]) });
    const first = style(point);
    expect(first).toBe(style(new Feature({ code: 0, geometry: new Point([1, 1]) })));
    expect(first.getStroke()?.getWidth()).toBe(4);
    expect((first.getImage() as CircleStyle).getRadius()).toBe(8);
    expect(first.getFill()?.getColor()).toBe('#100003');
    expect(style(new Feature({ code: 0, geometry: new LineString([[0, 0], [1, 1]]) })).getStroke()?.getColor()).toBe('#100003');
  });
  it('changes viewport fields only when the effective renderer field changes', () => {
    const spec = categorized();
    expect(activeCategoryField(layer)).toBe('legacy');
    expect(activeCategoryField({ ...layer, cartography: spec })).toBe('code');
    expect(activeCategoryField({ ...layer, cartography: { ...spec, symbol: { ...spec.symbol, fillColor: '#ffffff' } } })).toBe('code');
    expect(activeCategoryField({ ...layer, cartography: { ...spec, renderer: { kind: 'single' } } })).toBeNull();
  });
  it('rejects duplicate typed categories, unsafe numbers, missing fields and excess serialized size', () => {
    const spec = categorized();
    expect(cartographyValidationError(spec, dataset)).toBeNull();
    if (spec.renderer.kind !== 'categorized') throw new Error('fixture');
    expect(cartographyValidationError({ ...spec, renderer: { ...spec.renderer, categories: [...spec.renderer.categories, spec.renderer.categories[0]] } }, dataset)).toBeTruthy();
    expect(cartographyValidationError({ ...spec, renderer: { ...spec.renderer, categories: [{ value: 1e20, label: 'large', color: '#123456' }] } }, dataset)).toBeTruthy();
    expect(cartographyValidationError({ ...spec, renderer: { ...spec.renderer, field: 'missing' } }, dataset)).toBeTruthy();
    expect(cartographyValidationError({ ...spec, renderer: { ...spec.renderer, categories: Array.from({ length: 40 }, (_, i) => ({ value: `${i}${'中'.repeat(1000)}`, label: 'large', color: '#123456' })) } }, dataset)).toBeTruthy();
  });
  it('preserves backend-valid blank text and measures text budgets in Unicode code points', () => {
    const single = makeCartographySpec(dataset, 'planning', '😀'.repeat(121), 1);
    expect(single.legend.title).toBe('😀'.repeat(120));
    expect(cartographyValidationError({ ...single, legend: { visible: false, title: '' } }, dataset)).toBeNull();
    const spec = categorized();
    if (spec.renderer.kind !== 'categorized') throw new Error('fixture');
    spec.legend.title = '😀'.repeat(80);
    spec.renderer.nullLabel = '';
    spec.renderer.otherLabel = '  ';
    spec.renderer.categories = [{ value: '😀'.repeat(1024), label: '', color: '#123456' }];
    expect(cartographyValidationError(spec, dataset)).toBeNull();
    spec.renderer.categories[0].value += '😀';
    expect(cartographyValidationError(spec, dataset)).toBeTruthy();
  });
});
