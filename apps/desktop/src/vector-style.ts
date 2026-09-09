import type { FieldValue, MapLayer } from '../../../shared/contracts';

const palette = ['#417f69', '#5476ab', '#b2665b', '#b18a38', '#8774a5', '#548c93', '#8b9660', '#ac6c92'];

export function categoryKey(value: FieldValue | undefined): string {
  return value === null || value === undefined ? 'null' : String(value);
}

export function categoryColor(layer: MapLayer, value: FieldValue | undefined): string {
  if (!layer.categoryField) return layer.color;
  const key = categoryKey(value);
  if (layer.categoryColors[key]) return layer.categoryColors[key];
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
