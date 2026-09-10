import { expect, it } from 'vitest';
import type { DisplayFeature } from '../../../shared/contracts';
import { boundedDisplayLayers, boundedFeatures, viewportLayerBudget } from './vector-budget';

it('bounds aggregate layers and rejects geometry above remaining display vertex budget', () => {
  expect(viewportLayerBudget(20)).toEqual({ layers: 8, perLayer: 250 });
  const feature = (id: string, count: number) => ({ id, type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: Array.from({ length: count }, () => [0, 0]) } } as DisplayFeature);
  const result = boundedFeatures([feature('large', 21), feature('small', 4)], 20);
  expect(result.features.map((item) => item.id)).toEqual(['small']);
  expect(result.vertices).toBe(4);
  expect(result.truncated).toBe(true);
});

it('shares the eight layer request budget across raster and vector layers', () => {
  const layers = Array.from({ length: 64 }, (_, index) => ({ id: index, kind: index % 2 ? 'raster' : 'vector' }));
  const active = boundedDisplayLayers(layers);
  expect(active.filter((item) => item.kind === 'raster')).toHaveLength(4);
  expect(active.filter((item) => item.kind === 'vector')).toHaveLength(4);
  expect(active.map((item) => item.id)).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
});
