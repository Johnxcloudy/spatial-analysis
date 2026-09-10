import { useEffect, useRef } from 'react';
import { Crosshair, Minus, Plus, Scan } from 'lucide-react';
import Map from 'ol/Map';
import View from 'ol/View';
import GeoJSON from 'ol/format/GeoJSON';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import { Fill, Stroke, Style } from 'ol/style';
import type { ProbeReport } from '../../../../shared/contracts';

const sourceStyle = new Style({ fill: new Fill({ color: 'rgba(109, 129, 122, 0.16)' }), stroke: new Stroke({ color: '#61766f', width: 2 }) });
const intersectionStyle = new Style({ fill: new Fill({ color: 'rgba(17, 132, 93, 0.48)' }), stroke: new Stroke({ color: '#086945', width: 2 }) });

function fitGeometry(map: Map, source: VectorSource, duration = 0) {
  const extent = source.getExtent();
  if (!source.getFeatures().length || !extent) return;
  map.getView().cancelAnimations();
  map.getView().fit(extent, { padding: [60, 60, 60, 60], maxZoom: 19, duration });
}

export function ProjectionPreview({ report }: { report: ProbeReport | null }) {
  const target = useRef<HTMLDivElement>(null);
  const map = useRef<Map | null>(null);
  const source = useRef<VectorSource | null>(null);
  const fit = () => {
    if (map.current && source.current) fitGeometry(map.current, source.current, 180);
  };
  useEffect(() => {
    if (!report || !target.current) return;
    const features = new GeoJSON().readFeatures(report.preview, { dataProjection: 'EPSG:4326', featureProjection: 'EPSG:3857' });
    source.current = new VectorSource({ features });
    const instance = new Map({
      target: target.current,
      controls: [],
      layers: [new VectorLayer({ source: source.current, style: (feature) => feature.get('role') === 'intersection' ? intersectionStyle : sourceStyle })],
      view: new View({ projection: 'EPSG:3857', center: [0, 0], zoom: 1, minZoom: 1, maxZoom: 24, enableRotation: false }),
    });
    map.current = instance;
    instance.updateSize();
    const previewSource = source.current;
    fitGeometry(instance, previewSource);
    const observer = new ResizeObserver(() => {
      instance.updateSize();
      fitGeometry(instance, previewSource);
    });
    observer.observe(target.current);
    return () => { observer.disconnect(); instance.setTarget(undefined); instance.dispose(); map.current = null; source.current = null; };
  }, [report]);

  const zoom = (step: number) => {
    const view = map.current?.getView();
    if (view) view.animate({ zoom: (view.getZoom() ?? 1) + step, duration: 180 });
  };

  return <section className="preview-section" aria-label="投影验证">
    <div className="section-heading"><h2>投影验证</h2><span className="muted small">显示 CRS · EPSG:3857</span></div>
    {report ? <>
      <div className="projection-map" data-testid="projection-map">
        <div className="map-target" ref={target} tabIndex={0} aria-label="诊断几何投影预览" />
        <div className="map-controls"><button className="icon-button" title="放大" aria-label="放大" onClick={() => zoom(1)}><Plus size={18} /></button><button className="icon-button" title="缩小" aria-label="缩小" onClick={() => zoom(-1)}><Minus size={18} /></button><button className="icon-button" title="缩放至诊断几何" aria-label="缩放至诊断几何" onClick={fit}><Scan size={18} /></button></div>
        <div className="map-legend"><span><i className="swatch source" />测试矩形</span><span><i className="swatch intersection" />相交区域</span></div>
      </div>
      <div className="preview-caption"><span>源 CRS · {report.crs}</span><span>合成诊断样本</span></div>
    </> : <div className="empty-preview"><Crosshair size={30} strokeWidth={1.3} /><span>暂无投影验证结果</span></div>}
  </section>;
}
