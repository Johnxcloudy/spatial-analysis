import { cleanup, render, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { MapLayer, VectorDataset } from '../../../shared/contracts';
import type { DesktopBridge } from './bridge';
import { VectorMap } from './components/VectorMap';
import { makeCartographySpec } from './vector-style';
import VectorLayer from 'ol/layer/Vector';

const mapState = vi.hoisted(() => ({ layers: [] as unknown[] }));

vi.mock('ol/Map', () => ({ default: class {
  options: { view: unknown; layers: unknown[] };
  constructor(options: { view: unknown; layers: unknown[] }) { this.options = options; mapState.layers = options.layers; }
  getView() { return this.options.view; }
  getSize() { return [600, 400]; }
  getInteractions() { return { forEach: () => {} }; }
  addLayer(layer: unknown) { this.options.layers.push(layer); }
  removeLayer() {}
  updateSize() {}
  on() {}
  dispose() {}
} }));
vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
afterEach(cleanup);

it('requests only the effective category field and does not refetch geometry for symbol edits', async () => {
  const dataset = { id: 'land', version: 'v1', kind: 'vector', geometryType: 'Polygon', boundsWgs84: [0, 0, 1, 1], crsWkt: 'EPSG:4326' } as VectorDataset;
  const spec = { ...makeCartographySpec(dataset, 'planning', '配置', 1), renderer: { kind: 'categorized' as const, field: 'code', categories: [], nullColor: '#aaaaaa', nullLabel: 'NULL', otherColor: '#bbbbbb', otherLabel: '其他' } };
  const layer: MapLayer = { id: 'layer', datasetId: dataset.id, name: 'Land', visible: true, opacity: 1, color: '#123456', categoryField: 'legacy', categoryColors: {}, order: 0, cartography: spec };
  const request = vi.fn(async (_method: string, _params: unknown, _signal?: AbortSignal) => ({ datasetId: dataset.id, version: dataset.version, collection: { type: 'FeatureCollection', features: [] }, truncated: false, returnedCount: 0 }));
  const props = { bridge: { request } as unknown as DesktopBridge, path: 'test/project.spa', datasets: [dataset], initialView: { center: [0, 0] as [number, number], zoom: 5 }, enabled: true, selected: null, fitRequest: null, onSelect: vi.fn(), onViewChange: vi.fn(), onFailure: vi.fn() };
  const { rerender } = render(<VectorMap {...props} layers={[layer]} />);
  await waitFor(() => expect(request).toHaveBeenCalledOnce());
  expect(request.mock.calls[0][1]).toMatchObject({ propertyFields: ['code'], limit: 2000 });
  rerender(<VectorMap {...props} layers={[{ ...layer, cartography: { ...spec, revision: 2, symbol: { ...spec.symbol, strokeWidthPt: 4 } } }]} />);
  await new Promise((resolve) => setTimeout(resolve, 240));
  expect(request).toHaveBeenCalledOnce();
  rerender(<VectorMap {...props} layers={[{ ...layer, cartography: { ...spec, revision: 3, renderer: { ...spec.renderer, field: 'second' } } }]} />);
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  expect(request.mock.calls[1][1]).toMatchObject({ propertyFields: ['second'] });
});

it.each(['geometry', 'hasOwnProperty', '__proto__', 'constructor', '__spatialId', '__spatialGeometry', '__spatialProperties'])(
  'retains geometry, IDs and the category value of adversarial field %s', async (field) => {
    const dataset = { id: 'land', version: 'v1', kind: 'vector', geometryType: 'GeometryCollection', boundsWgs84: [0, 0, 1, 1], crsWkt: 'EPSG:4326' } as VectorDataset;
    const spec = { ...makeCartographySpec(dataset, 'planning', 'Configured', 1), renderer: {
      kind: 'categorized' as const, field, categories: [{ value: '01', label: 'Target', color: '#cc1122' }],
      nullColor: '#aaaaaa', nullLabel: 'NULL', otherColor: '#bbbbbb', otherLabel: 'Other',
    } };
    const layer: MapLayer = { id: 'layer', datasetId: dataset.id, name: 'Land', visible: true, opacity: 1,
      color: '#123456', categoryField: null, categoryColors: {}, order: 0, cartography: spec };
    const properties = Object.fromEntries([['geometry', 'unrelated'], ['hasOwnProperty', 'unrelated'],
      ['__proto__', 'unrelated'], ['__spatialId', 'attribute-id'], ['__spatialGeometry', 'unrelated'],
      ['__spatialProperties', 'unrelated'], [field, '01']]);
    const feature = { type: 'Feature' as const, id: '9007199254740993', properties,
      geometry: { type: 'GeometryCollection', geometries: [{ type: 'Point', coordinates: [0, 0] }, { type: 'LineString', coordinates: [[0, 0], [1, 1]] }] } };
    const request = vi.fn(async () => ({ datasetId: dataset.id, version: dataset.version,
      collection: { type: 'FeatureCollection', features: [feature] }, truncated: false, returnedCount: 1 }));
    const selected = { datasetId: dataset.id, version: dataset.version, feature,
      row: { id: feature.id, values: properties }, boundsWgs84: [0, 0, 1, 1] as [number, number, number, number] };
    render(<VectorMap bridge={{ request } as unknown as DesktopBridge} path="test/project.spa" datasets={[dataset]}
      layers={[layer]} initialView={{ center: [0, 0], zoom: 5 }} enabled selected={selected} fitRequest={null}
      onSelect={vi.fn()} onViewChange={vi.fn()} onFailure={vi.fn()} />);
    await waitFor(() => {
      const display = mapState.layers.find((item) => item instanceof VectorLayer && item.get('workspaceLayerId') === layer.id) as VectorLayer;
      const features = display.getSource()!.getFeatures();
      expect(features).toHaveLength(1);
      expect(features[0].getGeometry()!.getType()).toBe('GeometryCollection');
      expect(features[0].getId()).toBe('9007199254740993');
      const style = display.getStyleFunction()!(features[0], 1);
      expect(!Array.isArray(style) && style?.getFill()?.getColor()).toBe('#cc1122');
      const highlight = mapState.layers[0] as VectorLayer;
      expect(highlight.getSource()!.getFeatures()[0].getGeometry()!.getType()).toBe('GeometryCollection');
      expect(highlight.getSource()!.getFeatures()[0].getId()).toBe('9007199254740993');
    });
  },
);
