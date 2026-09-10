import type { DisplayFeature } from '../../../shared/contracts';

export const DISPLAY_VERTEX_BUDGET = 40000;
export const boundedDisplayLayers = <T,>(layers: readonly T[]): T[] => layers.slice(0, 8);
export function viewportLayerBudget(count: number) {
  const layers = Math.min(8, Math.max(0, count));
  return { layers, perLayer: layers ? Math.floor(2000 / layers) : 0 };
}
export function boundedFeatures(features: DisplayFeature[], budget: number) {
  let vertices = 0;
  let truncated = false;
  const accepted: DisplayFeature[] = [];
  for (const feature of features) {
    const stack: unknown[] = [feature.geometry];
    let count = 0;
    while (stack.length && count <= budget - vertices) {
      const item = stack.pop();
      if (Array.isArray(item)) {
        if (typeof item[0] === 'number') count += 1;
        else for (const child of item) stack.push(child);
      } else if (item && typeof item === 'object') {
        const geometry = item as { coordinates?: unknown; geometries?: unknown };
        stack.push(geometry.coordinates, geometry.geometries);
      }
    }
    if (count > budget - vertices) { truncated = true; continue; }
    vertices += count;
    accepted.push(feature);
  }
  return { features: accepted, vertices, truncated };
}
