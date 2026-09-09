import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Crosshair, Minus, Plus } from 'lucide-react';
import Map from 'ol/Map';
import View from 'ol/View';
import GeoJSON from 'ol/format/GeoJSON';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import { Circle as CircleStyle, Fill, Stroke, Style } from 'ol/style';
import { fromLonLat, toLonLat, transformExtent } from 'ol/proj';
import { ScaleLine } from 'ol/control';
import type { Bounds, FeatureResult, FieldValue, MapLayer, VectorDataset, ViewState } from '../../../../shared/contracts';
import type { DesktopBridge } from '../bridge';
import { normalizeError } from '../bridge';
import { categoryColor, translucent } from '../vector-style';

export interface FitRequest { key: number; bounds: Bounds }

const selectedStyle = new Style({
  fill: new Fill({ color: 'rgba(238,181,54,0.24)' }),
  stroke: new Stroke({ color: '#d38317', width: 3 }),
  image: new CircleStyle({ radius: 7, fill: new Fill({ color: '#edb540' }), stroke: new Stroke({ color: '#583c1e', width: 2 }) }),
});

function geographicBounds(map: Map): Bounds | null {
  const size = map.getSize();
  if (!size?.[0] || !size[1]) return null;
  const bounds = transformExtent(map.getView().calculateExtent(size), 'EPSG:3857', 'EPSG:4326');
  const result: Bounds = [Math.max(-180, bounds[0]), Math.max(-85.05112878, bounds[1]), Math.min(180, bounds[2]), Math.min(85.05112878, bounds[3])];
  return result.every(Number.isFinite) && result[0] < result[2] && result[1] < result[3] ? result : null;
}

export function VectorMap({ bridge, path, layers, datasets, initialView, enabled, selected, fitRequest, onSelect, onViewChange, onFailure }: {
  bridge: DesktopBridge;
  path?: string;
  layers: MapLayer[];
  datasets: VectorDataset[];
  initialView: ViewState;
  enabled: boolean;
  selected: FeatureResult | null;
  fitRequest: FitRequest | null;
  onSelect: (layerId: string, featureId: string) => void;
  onViewChange: (view: ViewState) => void;
  onFailure: (cause: unknown) => void;
}) {
  const target = useRef<HTMLDivElement>(null);
  const instance = useRef<Map | null>(null);
  const mapLayers = useRef(new globalThis.Map<string, VectorLayer<VectorSource>>());
  const highlight = useRef<VectorSource | null>(null);
  const callbacks = useRef({ onSelect, onViewChange, onFailure, enabled });
  callbacks.current = { onSelect, onViewChange, onFailure, enabled };
  const [bbox, setBbox] = useState<Bounds | null>(null);
  const [loading, setLoading] = useState(false);
  const [issues, setIssues] = useState<string[]>([]);
  const [coordinate, setCoordinate] = useState('');
  const datasetMap = new globalThis.Map(datasets.map((dataset) => [dataset.id, dataset]));
  const visible = layers.filter((layer) => layer.visible && datasetMap.get(layer.datasetId)?.boundsWgs84);
  const queryKey = JSON.stringify(visible.map((layer) => [layer.id, layer.datasetId, datasetMap.get(layer.datasetId)?.version, layer.categoryField]));
  const bboxKey = JSON.stringify(bbox);

  useEffect(() => {
    if (!target.current) return;
    const selectedSource = new VectorSource({ wrapX: false });
    const map = new Map({
      target: target.current,
      controls: [new ScaleLine({ units: 'metric' })],
      layers: [new VectorLayer({ source: selectedSource, style: selectedStyle, zIndex: 10000 })],
      view: new View({ projection: 'EPSG:3857', center: fromLonLat(initialView.center), zoom: initialView.zoom, minZoom: 1, maxZoom: 24, enableRotation: false, multiWorld: false }),
    });
    instance.current = map;
    highlight.current = selectedSource;
    map.on('moveend', () => {
      setBbox(geographicBounds(map));
      const center = toLonLat(map.getView().getCenter() ?? [0, 0]);
      if (callbacks.current.enabled) callbacks.current.onViewChange({ center: [Number(center[0].toFixed(9)), Number(center[1].toFixed(9))], zoom: Number((map.getView().getZoom() ?? 5).toFixed(6)) });
    });
    map.on('pointermove', (event) => {
      const coordinates = toLonLat(event.coordinate);
      setCoordinate(`${coordinates[0].toFixed(5)}, ${coordinates[1].toFixed(5)}`);
    });
    map.on('singleclick', (event) => {
      if (!callbacks.current.enabled) return;
      map.forEachFeatureAtPixel(event.pixel, (feature, layer) => {
        const layerId = layer?.get('workspaceLayerId') as string | undefined;
        const id = feature.getId();
        if (layerId && id !== undefined) { callbacks.current.onSelect(layerId, String(id)); return true; }
        return undefined;
      }, { hitTolerance: 5 });
    });
    const resize = () => { map.updateSize(); setBbox(geographicBounds(map)); };
    const observer = new ResizeObserver(resize);
    observer.observe(target.current);
    resize();
    return () => { observer.disconnect(); map.dispose(); instance.current = null; highlight.current = null; mapLayers.current.clear(); };
  }, []);

  useEffect(() => {
    const map = instance.current;
    if (!map) return;
    for (const [id, layer] of mapLayers.current) {
      if (!layers.some((item) => item.id === id)) { map.removeLayer(layer); mapLayers.current.delete(id); }
    }
    layers.forEach((definition, index) => {
      let layer = mapLayers.current.get(definition.id);
      if (!layer) {
        layer = new VectorLayer({ source: new VectorSource({ wrapX: false }) });
        layer.set('workspaceLayerId', definition.id);
        mapLayers.current.set(definition.id, layer);
        map.addLayer(layer);
      }
      layer.setVisible(definition.visible && !!datasetMap.get(definition.datasetId)?.boundsWgs84);
      layer.setOpacity(definition.opacity);
      layer.setZIndex(layers.length - index);
      const styles = new globalThis.Map<string, Style>();
      layer.setStyle((feature) => {
        const color = categoryColor(definition, definition.categoryField ? feature.get(definition.categoryField) as FieldValue : undefined);
        if (!styles.has(color)) styles.set(color, new Style({ fill: new Fill({ color: translucent(color, 0.33) }), stroke: new Stroke({ color, width: 1.6 }), image: new CircleStyle({ radius: 5, fill: new Fill({ color: translucent(color, 0.8) }), stroke: new Stroke({ color: '#ffffff', width: 1.1 }) }) }));
        return styles.get(color)!;
      });
    });
  }, [layers, datasets]);

  useEffect(() => {
    let cancelled = false;
    setIssues([]);
    setLoading(false);
    if (!enabled || !path || !bbox || !visible.length) return;
    const timer = setTimeout(() => {
      setLoading(true);
      void (async () => {
        const notices: string[] = [];
        for (const layer of visible) {
          if (cancelled) return;
          try {
            const dataset = datasetMap.get(layer.datasetId)!;
            const result = await bridge.request('vector.viewport', { path, datasetId: dataset.id, bbox, limit: 2000, propertyFields: layer.categoryField ? [layer.categoryField] : [] });
            if (cancelled) return;
            if (result.datasetId !== dataset.id || result.version !== dataset.version) throw new Error('显示数据版本不匹配，请刷新工作区。');
            const source = mapLayers.current.get(layer.id)?.getSource();
            const features = new GeoJSON().readFeatures(result.collection, { dataProjection: 'EPSG:4326', featureProjection: 'EPSG:3857' });
            source?.clear();
            source?.addFeatures(features);
            if (result.truncated) notices.push(`${layer.name}：当前显示 ${result.returnedCount} 个要素，结果已截断`);
          } catch (cause) {
            if (cancelled) return;
            mapLayers.current.get(layer.id)?.getSource()?.clear();
            notices.push(`${layer.name}：${normalizeError(cause).message}`);
            if (normalizeError(cause).data?.kind?.startsWith('ENGINE_')) callbacks.current.onFailure(cause);
          }
        }
        if (!cancelled) { setIssues(notices); setLoading(false); }
      })();
    }, 180);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [bridge, path, queryKey, bboxKey, enabled]);

  useEffect(() => {
    highlight.current?.clear();
    if (selected?.feature) {
      const feature = new GeoJSON().readFeature(selected.feature, { dataProjection: 'EPSG:4326', featureProjection: 'EPSG:3857' });
      highlight.current?.addFeatures(Array.isArray(feature) ? feature : [feature]);
    }
  }, [selected]);

  useEffect(() => {
    if (!fitRequest || !instance.current) return;
    const [west, south, east, north] = fitRequest.bounds;
    if (![west, south, east, north].every(Number.isFinite)) return;
    const extent = transformExtent([west, Math.max(south, -85.05112878), east, Math.min(north, 85.05112878)], 'EPSG:4326', 'EPSG:3857');
    instance.current.getView().fit(extent, { padding: [45, 45, 45, 45], maxZoom: 18, duration: 180 });
  }, [fitRequest?.key]);

  const zoom = (amount: number) => { const view = instance.current?.getView(); if (view) view.animate({ zoom: (view.getZoom() ?? 5) + amount, duration: 150 }); };
  return <div className="vector-map" data-testid="vector-map">
    <div className="map-target" ref={target} tabIndex={0} aria-label="矢量地图" />
    {!visible.length && <div className="map-empty"><Crosshair size={30} strokeWidth={1.3} /><span>{layers.length ? '没有可显示的图层' : '未添加图层'}</span></div>}
    <div className="map-controls"><button className="icon-button" aria-label="地图放大" title="地图放大" onClick={() => zoom(1)}><Plus size={18} /></button><button className="icon-button" aria-label="地图缩小" title="地图缩小" onClick={() => zoom(-1)}><Minus size={18} /></button></div>
    {loading && <span className="map-loading" role="status">读取显示数据</span>}
    {!!issues.length && <div className="map-notices" role="status"><AlertTriangle size={15} /><div>{issues.map((issue) => <p key={issue}>{issue}</p>)}</div></div>}
    <div className="map-coordinate">{coordinate || '显示 CRS：EPSG:3857'}</div>
  </div>;
}
