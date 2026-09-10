import { Circle as CircleStyle, Fill, Stroke, Style } from 'ol/style';
import type { FeatureLike } from 'ol/Feature';
import type { FieldValue, MapLayer, VectorCartographySpec, VectorDataset } from '../../../shared/contracts';

const palette = ['#417f69', '#5476ab', '#b2665b', '#b18a38', '#8774a5', '#548c93', '#8b9660', '#ac6c92'];

export function categoryKey(value: FieldValue | undefined): string {
  return value === null || value === undefined ? 'null' : String(value);
}

export function categoryColor(layer: MapLayer, value: FieldValue | undefined): string {
  if (!layer.categoryField) return layer.color;
  const key = categoryKey(value);
  if (Object.prototype.hasOwnProperty.call(layer.categoryColors, key)) return layer.categoryColors[key];
  let hash = 0;
  for (const character of key) hash = ((hash << 5) - hash + character.charCodeAt(0)) | 0;
  return palette[(hash >>> 0) % palette.length];
}

export function translucent(color: string, alpha: number): string {
  const channels = color.replace('#', '').match(/.{2}/g)?.map((part) => parseInt(part, 16));
  return channels?.length === 3 ? `rgba(${channels.join(',')},${alpha})` : color;
}

export function displayValue(value: FieldValue | undefined): string {
  if (value === null || value === undefined) return 'NULL';
  if (value === '') return '""';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return String(value);
}

export const POINTS_TO_CSS_PIXELS = 96 / 72;
export const CATEGORY_LIMIT = 64;
export const presetPalettes = {
  planning: ['#7eac84', '#e2b96f', '#a18fbf', '#75a9c5', '#ce8d7e', '#b7c778', '#cd9cb6', '#84b7b0'],
  publication: ['#4c78a8', '#f2a541', '#65a17b', '#b47c99', '#7d91a5', '#c68b68', '#8e8bb7', '#82b4b0'],
} as const;

export function makeCartographySpec(dataset: Pick<VectorDataset, 'id' | 'version'>, preset: VectorCartographySpec['basePreset'], title: string, revision: number): VectorCartographySpec {
  return {
    specVersion: 1, kind: 'vector-layer', revision,
    input: { datasetId: dataset.id, version: dataset.version }, basePreset: preset, presetVersion: 1,
    symbol: preset === 'planning'
      ? { fillColor: '#7eac84', strokeColor: '#385b43', strokeWidthPt: 0.8, pointRadiusPt: 4 }
      : { fillColor: '#4c78a8', strokeColor: '#374151', strokeWidthPt: 0.45, pointRadiusPt: 3 },
    renderer: { kind: 'single' }, legend: { visible: true, title: Array.from(title).slice(0, 120).join('') },
  };
}

export function typedCategoryKey(value: FieldValue | undefined): string {
  return value === undefined ? 'undefined' : `${typeof value}:${JSON.stringify(value)}`;
}

export function typedCategoryLabel(value: Exclude<FieldValue, null>): string {
  return `${typeof value === 'string' ? '文字' : typeof value === 'number' ? '数值' : '布尔'} ${JSON.stringify(value)}`;
}

export function activeCategoryField(layer: MapLayer): string | null {
  return layer.cartography ? (layer.cartography.renderer.kind === 'categorized' ? layer.cartography.renderer.field : null) : layer.categoryField;
}

export interface VectorSymbol {
  fillColor: string;
  pointFillColor: string;
  strokeColor: string;
  pointStrokeColor: string;
  lineColor: string;
  strokeWidthPx: number;
  pointStrokeWidthPx: number;
  pointRadiusPx: number;
}

function resolvedSymbol(spec: VectorCartographySpec, fillColor: string): VectorSymbol {
  return { fillColor, pointFillColor: fillColor, strokeColor: spec.symbol.strokeColor, pointStrokeColor: spec.symbol.strokeColor,
    lineColor: spec.renderer.kind === 'categorized' ? fillColor : spec.symbol.strokeColor,
    strokeWidthPx: spec.symbol.strokeWidthPt * POINTS_TO_CSS_PIXELS,
    pointStrokeWidthPx: spec.symbol.strokeWidthPt * POINTS_TO_CSS_PIXELS,
    pointRadiusPx: spec.symbol.pointRadiusPt * POINTS_TO_CSS_PIXELS };
}

export function compileVectorStyle(layer: MapLayer): { field: string | null; resolve: (value: FieldValue | undefined) => VectorSymbol } {
  const spec = layer.cartography;
  if (!spec) return { field: layer.categoryField, resolve: (value) => {
    const color = categoryColor(layer, value);
    return { fillColor: translucent(color, 0.33), pointFillColor: translucent(color, 0.8), strokeColor: color,
      lineColor: color, pointStrokeColor: '#ffffff', strokeWidthPx: 1.6, pointStrokeWidthPx: 1.1, pointRadiusPx: 5 };
  } };
  const renderer = spec.renderer;
  if (renderer.kind === 'single') {
    const symbol = resolvedSymbol(spec, spec.symbol.fillColor);
    return { field: null, resolve: () => symbol };
  }
  const categories = new Map(renderer.categories.map((category) => [typedCategoryKey(category.value), resolvedSymbol(spec, category.color)]));
  const nullSymbol = resolvedSymbol(spec, renderer.nullColor);
  const otherSymbol = resolvedSymbol(spec, renderer.otherColor);
  return { field: renderer.field, resolve: (value) => value === null ? nullSymbol : categories.get(typedCategoryKey(value)) ?? otherSymbol };
}

// Attribute names belong to the dataset, not OpenLayers' property namespace.
// A WeakMap also avoids reserving a synthetic field name in imported datasets.
const displayProperties = new WeakMap<FeatureLike, Readonly<Record<string, FieldValue>>>();

export function setVectorFeatureProperties(feature: FeatureLike, properties: Readonly<Record<string, FieldValue>>): void {
  displayProperties.set(feature, properties);
}

export function vectorFeatureValue(feature: FeatureLike, field: string): FieldValue | undefined {
  const properties = displayProperties.get(feature);
  if (properties) return Object.prototype.hasOwnProperty.call(properties, field) ? properties[field] : undefined;
  return feature.get(field) as FieldValue | undefined;
}

export function createVectorStyleFunction(layer: MapLayer): (feature: FeatureLike) => Style {
  const compiled = compileVectorStyle(layer);
  const cache = new Map<string, Style>();
  return (feature) => {
    const symbol = compiled.resolve(compiled.field ? vectorFeatureValue(feature, compiled.field) : undefined);
    const line = /LineString/.test(feature.getGeometry()?.getType() ?? '');
    const key = `${line ? 'line' : 'area'}:${symbol.fillColor}`;
    let style = cache.get(key);
    if (!style) {
      style = new Style({
        fill: new Fill({ color: symbol.fillColor }),
        stroke: symbol.strokeWidthPx > 0 ? new Stroke({ color: line ? symbol.lineColor : symbol.strokeColor, width: symbol.strokeWidthPx }) : undefined,
        image: new CircleStyle({ radius: symbol.pointRadiusPx, fill: new Fill({ color: symbol.pointFillColor }),
          stroke: symbol.pointStrokeWidthPx > 0 ? new Stroke({ color: symbol.pointStrokeColor, width: symbol.pointStrokeWidthPx }) : undefined }),
      });
      cache.set(key, style);
    }
    return style;
  };
}

export function vectorLegendEntries(spec: VectorCartographySpec): { key: string; label: string; valueLabel: string; symbol: VectorSymbol }[] {
  const renderer = spec.renderer;
  if (renderer.kind === 'single') return [{ key: 'single', label: '全部要素', valueLabel: '单一符号', symbol: resolvedSymbol(spec, spec.symbol.fillColor) }];
  return [
    ...renderer.categories.map((entry) => ({ key: typedCategoryKey(entry.value), label: entry.label, valueLabel: typedCategoryLabel(entry.value), symbol: resolvedSymbol(spec, entry.color) })),
    { key: 'null', label: renderer.nullLabel, valueLabel: 'NULL', symbol: resolvedSymbol(spec, renderer.nullColor) },
    { key: 'other', label: renderer.otherLabel, valueLabel: '未配置值', symbol: resolvedSymbol(spec, renderer.otherColor) },
  ];
}

/** Early form feedback only; the engine owns strict persistence validation. */
export function cartographyValidationError(spec: VectorCartographySpec, dataset: VectorDataset): string | null {
  const validColor = (color: string) => /^#[0-9a-f]{6}$/i.test(color);
  const validLabel = (label: string) => Array.from(label).length <= 120;
  if (spec.input.datasetId !== dataset.id || spec.input.version !== dataset.version) return '样式引用的数据版本已改变，请重新载入。';
  if (!validColor(spec.symbol.fillColor) || !validColor(spec.symbol.strokeColor)) return '颜色必须为六位十六进制颜色。';
  if (!Number.isFinite(spec.symbol.strokeWidthPt) || spec.symbol.strokeWidthPt < 0 || spec.symbol.strokeWidthPt > 6 || !Number.isFinite(spec.symbol.pointRadiusPt) || spec.symbol.pointRadiusPt < 1 || spec.symbol.pointRadiusPt > 16) return '线宽须为 0–6 pt，点半径须为 1–16 pt。';
  if (!validLabel(spec.legend.title)) return '图例标题最多 120 个字符。';
  if (spec.renderer.kind === 'categorized') {
    const renderer = spec.renderer;
    if (!dataset.fields.some((field) => field.name === renderer.field)) return '请选择当前数据中的分类字段。';
    if (renderer.categories.length > CATEGORY_LIMIT) return `最多配置 ${CATEGORY_LIMIT} 个类别。`;
    if (!validLabel(renderer.nullLabel) || !validLabel(renderer.otherLabel) || !validColor(renderer.nullColor) || !validColor(renderer.otherColor)) return 'NULL 与其他值须有有效标签和颜色。';
    const keys = new Set<string>();
    for (const category of renderer.categories) {
      if (typeof category.value === 'number' && (!Number.isFinite(category.value) || Math.abs(category.value) > Number.MAX_SAFE_INTEGER)) return '数值类别须为有限安全数值；较大整数请使用文字。';
      if (typeof category.value === 'string' && Array.from(category.value).length > 1024) return '文字类别值最多 1024 个字符。';
      if (!validLabel(category.label) || !validColor(category.color)) return '类别标签最多 120 个字符，并设置有效颜色。';
      const key = typedCategoryKey(category.value);
      if (keys.has(key)) return '同一类型和值的类别不能重复。';
      keys.add(key);
    }
  }
  if (new TextEncoder().encode(JSON.stringify(spec)).length > 32768) return '样式配置超过 32 KiB，请减少类别或缩短文字。';
  return null;
}
