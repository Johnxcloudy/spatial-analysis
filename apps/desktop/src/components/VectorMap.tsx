import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Crosshair, Minus, Plus } from 'lucide-react';
import Map from 'ol/Map';
import View from 'ol/View';
import GeoJSON from 'ol/format/GeoJSON';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import ImageLayer from 'ol/layer/Image';
import ImageStatic from 'ol/source/ImageStatic';
import { Circle as CircleStyle, Fill, Stroke, Style } from 'ol/style';
import { fromLonLat, toLonLat, transformExtent } from 'ol/proj';
import { ScaleLine } from 'ol/control';
import type { Bounds, FeatureResult, FieldValue, MapLayer, RasterDataset, VectorDataset, ViewState } from '../../../../shared/contracts';
import type { DesktopBridge } from '../bridge';
import { normalizeError } from '../bridge';
import { categoryColor, translucent } from '../vector-style';
import { createRasterRenderer, rasterFrame, type RasterFrame } from '../raster-display';
import { latestRequest } from '../latest-request';
import { boundedDisplayLayers, boundedFeatures, DISPLAY_VERTEX_BUDGET, viewportLayerBudget } from '../vector-budget';

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

export function VectorMap({ bridge, path, layers, datasets, initialView, enabled, locked = false, selected, fitRequest, onSelect, onSelectPixel, onViewChange, onFailure }: {
  bridge: DesktopBridge;
  path?: string;
  layers: MapLayer[];
  datasets: (VectorDataset | RasterDataset)[];
  initialView: ViewState;
  enabled: boolean;
  locked?: boolean;
  selected: FeatureResult | null;
  fitRequest: FitRequest | null;
  onSelect: (layerId: string, featureId: string) => void;
  onSelectPixel?: (coordinate: [number, number]) => void;
  onViewChange: (view: ViewState) => void;
  onFailure: (cause: unknown) => void;
}) {
  const target = useRef<HTMLDivElement>(null);
  const instance = useRef<Map | null>(null);
  const mapLayers = useRef(new globalThis.Map<string, VectorLayer<VectorSource> | ImageLayer<ImageStatic>>());
  const rasterQueues = useRef(new globalThis.Map<string, ReturnType<typeof createRasterRenderer>>());
  const imageRevision = useRef(0);
  const vectorQueue = useRef<ReturnType<typeof latestRequest<() => Promise<void>, void>> | null>(null);
  const highlight = useRef<VectorSource | null>(null);
  const callbacks = useRef({ onSelect, onSelectPixel, onViewChange, onFailure, enabled });
  callbacks.current = { onSelect, onSelectPixel, onViewChange, onFailure, enabled };
  const [bbox, setBbox] = useState<Bounds | null>(null);
  const [loading, setLoading] = useState(false);
  const [issues, setIssues] = useState<string[]>([]);
  const [coordinate, setCoordinate] = useState('');
  const [frame, setFrame] = useState<RasterFrame | null>(null);
  const [rasterBusy, setRasterBusy] = useState<Set<string>>(() => new Set());
  const [rasterIssues, setRasterIssues] = useState<Record<string, string>>({});
  const datasetMap = new globalThis.Map(datasets.map((dataset) => [dataset.id, dataset]));
  const visible = layers.filter((layer) => layer.visible && datasetMap.get(layer.datasetId)?.boundsWgs84 && datasetMap.get(layer.datasetId)?.crsWkt);
  const displayed = boundedDisplayLayers(visible);
  const vectorLayers = displayed.filter((layer) => datasetMap.get(layer.datasetId)?.kind === 'vector');
  const rasterLayers = displayed.filter((layer) => datasetMap.get(layer.datasetId)?.kind === 'raster');
  const queryKey = JSON.stringify(vectorLayers.map((layer) => [layer.id, layer.datasetId, datasetMap.get(layer.datasetId)?.version, layer.categoryField]));
  const rasterKey = JSON.stringify(rasterLayers.map((layer) => [layer.id, layer.datasetId, datasetMap.get(layer.datasetId)?.version, layer.rasterStyle]));
  const bboxKey = JSON.stringify(bbox);
  const frameKey = JSON.stringify(frame);

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
    vectorQueue.current = latestRequest<() => Promise<void>, void>((request) => request(), { result: () => {}, error: (cause) => callbacks.current.onFailure(cause) });
    highlight.current = selectedSource;
    map.on('moveend', () => {
      setBbox(geographicBounds(map));
      const size = map.getSize();
      setFrame(size ? rasterFrame(map.getView().calculateExtent(size), size) : null);
      const center = toLonLat(map.getView().getCenter() ?? [0, 0]);
      if (callbacks.current.enabled) callbacks.current.onViewChange({ center: [Number(center[0].toFixed(9)), Number(center[1].toFixed(9))], zoom: Number((map.getView().getZoom() ?? 5).toFixed(6)) });
    });
    let coordinateFrame = 0;
    let latestCoordinate = '';
    map.on('pointermove', (event) => {
      const coordinates = toLonLat(event.coordinate);
      latestCoordinate = `${coordinates[0].toFixed(5)}, ${coordinates[1].toFixed(5)}`;
      if (!coordinateFrame) coordinateFrame = requestAnimationFrame(() => { coordinateFrame = 0; setCoordinate(latestCoordinate); });
    });
    map.on('singleclick', (event) => {
      if (!callbacks.current.enabled) return;
      if (callbacks.current.onSelectPixel) {
        const coordinate = toLonLat(event.coordinate);
        callbacks.current.onSelectPixel([coordinate[0], coordinate[1]]);
        return;
      }
      map.forEachFeatureAtPixel(event.pixel, (feature, layer) => {
        const layerId = layer?.get('workspaceLayerId') as string | undefined;
        const id = feature.getId();
        if (layerId && id !== undefined) { callbacks.current.onSelect(layerId, String(id)); return true; }
        return undefined;
      }, { hitTolerance: 5 });
    });
    const resize = () => { map.updateSize(); setBbox(geographicBounds(map)); const size = map.getSize(); setFrame(size ? rasterFrame(map.getView().calculateExtent(size), size) : null); };
    const observer = new ResizeObserver(resize);
    observer.observe(target.current);
    resize();
    return () => { cancelAnimationFrame(coordinateFrame); vectorQueue.current?.dispose(); vectorQueue.current = null; observer.disconnect(); for (const queue of rasterQueues.current.values()) queue.dispose(); rasterQueues.current.clear(); map.dispose(); instance.current = null; highlight.current = null; mapLayers.current.clear(); };
  }, []);

  useEffect(() => {
    const map = instance.current;
    if (!map) return;
    if (locked) map.getView().cancelAnimations();
    map.getInteractions().forEach((interaction) => interaction.setActive(!locked));
  }, [locked]);

  useEffect(() => {
    const map = instance.current;
    if (!map) return;
    for (const [id, layer] of mapLayers.current) {
      if (!layers.some((item) => item.id === id)) { map.removeLayer(layer); mapLayers.current.delete(id); rasterQueues.current.get(id)?.clear(); rasterQueues.current.get(id)?.dispose(); rasterQueues.current.delete(id); }
    }
    layers.forEach((definition, index) => {
      const dataset = datasetMap.get(definition.datasetId);
      let layer = mapLayers.current.get(definition.id);
      if (!layer) {
        layer = dataset?.kind === 'raster' ? new ImageLayer<ImageStatic>() : new VectorLayer({ source: new VectorSource({ wrapX: false }) });
        layer.set('workspaceLayerId', definition.id);
        mapLayers.current.set(definition.id, layer);
        map.addLayer(layer);
      }
      layer.setVisible(displayed.some((item) => item.id === definition.id));
      layer.setOpacity(definition.opacity);
      layer.setZIndex(layers.length - index);
      if (!(layer instanceof VectorLayer)) return;
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
    const controller = new AbortController();
    setIssues([]);
    setLoading(false);
    if (!enabled || !path || !bbox || !vectorLayers.length) return;
    const timer = setTimeout(() => {
      setLoading(true);
      vectorQueue.current?.push(async () => {
        const notices: string[] = [];
        const budget = viewportLayerBudget(vectorLayers.length);
        let remainingVertices = DISPLAY_VERTEX_BUDGET;
        for (const layer of vectorLayers.slice(budget.layers)) {
          const displayLayer = mapLayers.current.get(layer.id);
          if (displayLayer instanceof VectorLayer) displayLayer.getSource()?.clear();
        }
        if (vectorLayers.length > budget.layers) notices.push(`地图显示最多 ${budget.layers} 个矢量图层；请隐藏上层图层以查看其余图层。完整分析不受影响。`);
        for (const layer of vectorLayers.slice(0, budget.layers)) {
          if (cancelled) return;
          try {
            const dataset = datasetMap.get(layer.datasetId)!;
            const result = await bridge.request('vector.viewport', { path, datasetId: dataset.id, bbox, limit: budget.perLayer, propertyFields: layer.categoryField ? [layer.categoryField] : [] }, controller.signal);
            if (cancelled) return;
            if (result.datasetId !== dataset.id || result.version !== dataset.version) throw new Error('显示数据版本不匹配，请刷新工作区。');
            const displayLayer = mapLayers.current.get(layer.id);
            const source = displayLayer instanceof VectorLayer ? displayLayer.getSource() : null;
            const bounded = boundedFeatures(result.collection.features, remainingVertices);
            remainingVertices -= bounded.vertices;
            const features = new GeoJSON().readFeatures({ ...result.collection, features: bounded.features }, { dataProjection: 'EPSG:4326', featureProjection: 'EPSG:3857' });
            source?.clear();
            source?.addFeatures(features);
            if (result.truncated) notices.push(`${layer.name}：当前显示 ${result.returnedCount} 个要素，结果已截断`);
            if (bounded.truncated) notices.push(`${layer.name}：达到地图顶点预算，部分显示几何已省略；完整分析不受影响`);
          } catch (cause) {
            if (cancelled) return;
            const displayLayer = mapLayers.current.get(layer.id);
            if (displayLayer instanceof VectorLayer) displayLayer.getSource()?.clear();
            notices.push(`${layer.name}：${normalizeError(cause).message}`);
            if (normalizeError(cause).data?.kind?.startsWith('ENGINE_')) callbacks.current.onFailure(cause);
          }
        }
        if (!cancelled) { setIssues(notices); setLoading(false); }
      });
    }, 180);
    return () => { cancelled = true; controller.abort(); clearTimeout(timer); vectorQueue.current?.clear(); };
  }, [bridge, path, queryKey, bboxKey, enabled]);

  useEffect(() => {
    setRasterIssues({});
    if (!enabled || !path || !frame) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      for (const definition of rasterLayers) {
        const dataset = datasetMap.get(definition.datasetId);
        if (dataset?.kind !== 'raster' || !definition.rasterStyle) continue;
        let queue = rasterQueues.current.get(definition.id);
        if (!queue) {
          queue = createRasterRenderer(bridge, {
            result: (result) => {
              const layer = mapLayers.current.get(definition.id);
              if (!(layer instanceof ImageLayer)) return;
              const revision = imageRevision.current;
              const source = new ImageStatic({ url: `data:image/png;base64,${result.imageBase64}`, projection: 'EPSG:3857', imageExtent: result.bbox, interpolate: false });
              source.once('imageloaderror', () => { if (revision === imageRevision.current && mapLayers.current.get(definition.id) === layer && layer.getSource() === source) { layer.setSource(null); setRasterIssues((current) => ({ ...current, [definition.id]: `${definition.name}：显示图像无法解码` })); } });
              layer.setSource(source);
            },
            error: (cause) => {
              const layer = mapLayers.current.get(definition.id);
              if (layer instanceof ImageLayer) layer.setSource(null);
              const failure = normalizeError(cause);
              setRasterIssues((current) => ({ ...current, [definition.id]: `${definition.name}：${failure.message}` }));
              if (failure.data?.kind?.startsWith('ENGINE_')) callbacks.current.onFailure(cause);
            },
            busy: (busy) => setRasterBusy((current) => { const next = new Set(current); if (busy) next.add(definition.id); else next.delete(definition.id); return next; }),
          });
          rasterQueues.current.set(definition.id, queue);
        }
        queue.push({ path, datasetId: dataset.id, version: dataset.version, ...frame, style: definition.rasterStyle, signal: controller.signal });
      }
    }, 180);
    return () => { controller.abort(); imageRevision.current += 1; clearTimeout(timer); for (const queue of rasterQueues.current.values()) queue.clear(); };
  }, [bridge, path, rasterKey, frameKey, enabled]);

  useEffect(() => {
    highlight.current?.clear();
    if (selected?.feature) {
      const feature = new GeoJSON().readFeature(selected.feature, { dataProjection: 'EPSG:4326', featureProjection: 'EPSG:3857' });
      highlight.current?.addFeatures(Array.isArray(feature) ? feature : [feature]);
    }
  }, [selected]);

  useEffect(() => {
    if (locked || !fitRequest || !instance.current) return;
    const [west, south, east, north] = fitRequest.bounds;
    if (![west, south, east, north].every(Number.isFinite)) return;
    const extent = transformExtent([west, Math.max(south, -85.05112878), east, Math.min(north, 85.05112878)], 'EPSG:4326', 'EPSG:3857');
    instance.current.getView().fit(extent, { padding: [45, 45, 45, 45], maxZoom: 18, duration: 180 });
  }, [fitRequest?.key]);

  const zoom = (amount: number) => { const view = instance.current?.getView(); if (view) view.animate({ zoom: (view.getZoom() ?? 5) + amount, duration: 150 }); };
  return <div className="vector-map" data-testid="vector-map">
    <div className="map-target" ref={target} tabIndex={0} aria-label="地图" />
    {visible.length > displayed.length && <div className="map-budget-notice" role="status">当前仅显示前 {displayed.length} 个图层（矢量与栅格合计）；隐藏上层图层可查看其余图层。完整分析不受影响。</div>}
    {!visible.length && <div className="map-empty"><Crosshair size={30} strokeWidth={1.3} /><span>{layers.length ? '没有可显示的图层' : '未添加图层'}</span></div>}
    <div className="map-controls"><button className="icon-button" aria-label="地图放大" title="地图放大" disabled={locked} onClick={() => zoom(1)}><Plus size={18} /></button><button className="icon-button" aria-label="地图缩小" title="地图缩小" disabled={locked} onClick={() => zoom(-1)}><Minus size={18} /></button></div>
    {(loading || !!rasterBusy.size) && <span className="map-loading" role="status">读取显示数据</span>}
    {!!(issues.length + Object.keys(rasterIssues).length) && <div className="map-notices" role="status"><AlertTriangle size={15} /><div>{[...issues, ...Object.values(rasterIssues)].map((issue) => <p key={issue}>{issue}</p>)}</div></div>}
    <div className="map-coordinate">{coordinate || '显示 CRS：EPSG:3857'}</div>
  </div>;
}
