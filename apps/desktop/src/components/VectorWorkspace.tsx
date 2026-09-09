import { lazy, Suspense, useEffect, useState } from 'react';
import { Download, Info, LoaderCircle, Scan, Table2 } from 'lucide-react';
import type { DesktopBridge } from '../bridge';
import type { WorkspaceState } from '../use-workspace';
import { initialAttributeQuery, useAttributePage, useSelectedFeature } from '../use-vector-data';
import { AttributeTable } from './AttributeTable';
import { DatasetDetails } from './DatasetDetails';
import type { FitRequest } from './VectorMap';

const VectorMap = lazy(() => import('./VectorMap').then((module) => ({ default: module.VectorMap })));

export function VectorWorkspace({ bridge, state, fitRequest, onFit }: { bridge: DesktopBridge; state: WorkspaceState; fitRequest: FitRequest | null; onFit: (bounds: [number, number, number, number]) => void }) {
  const [query, setQuery] = useState(initialAttributeQuery);
  const [selection, setSelection] = useState<{ layerId: string; featureId: string } | null>(null);
  const [details, setDetails] = useState(false);
  const [fitSelected, setFitSelected] = useState(false);
  const layers = state.workspace?.layers ?? [];
  const datasets = state.workspace?.datasets ?? [];
  const layer = layers.find((item) => item.id === state.selectedLayerId);
  const selectedId = selection?.layerId === layer?.id ? selection?.featureId ?? null : null;
  const dataset = datasets.find((item) => item.id === layer?.datasetId);
  const enabled = !!state.project && !!state.runtime && !state.needsReopen && state.native;
  useEffect(() => {
    setQuery(initialAttributeQuery);
    setSelection((current) => current?.layerId === layer?.id ? current : null);
    setFitSelected(false);
  }, [layer?.id, dataset?.version]);
  const { page, loading } = useAttributePage(bridge, state.project?.projectPath, dataset, query, enabled, state.handleFailure);
  const { selected } = useSelectedFeature(bridge, state.project?.projectPath, dataset, selectedId, enabled, state.handleFailure);
  useEffect(() => { if (fitSelected && selected?.row.id === selectedId && selected?.boundsWgs84) { onFit(selected.boundsWgs84); setFitSelected(false); } }, [selected, selectedId, fitSelected, onFit]);
  return <div className={`vector-workspace ${details ? 'details-open' : ''}`}>
    <div className="map-workspace-main"><div className="map-toolbar"><span><Table2 size={15} /><strong>{layer?.name ?? '地图'}</strong></span><span className="map-crs-label">EPSG:3857</span><button className="icon-button" aria-label="定位当前图层" title="定位当前图层" disabled={!dataset?.boundsWgs84} onClick={() => dataset?.boundsWgs84 && onFit(dataset.boundsWgs84)}><Scan size={17} /></button><button className="icon-button" aria-label="导出当前数据" title="导出 GeoPackage" disabled={!dataset || !enabled || !!state.activeTask || !!state.busy} onClick={() => dataset && void state.exportVector(dataset.id, dataset.name)}><Download size={17} /></button><button className={`icon-button ${details ? 'pressed' : ''}`} aria-label="数据与检查报告" title="数据与检查报告" aria-pressed={details} onClick={() => setDetails(!details)}><Info size={17} /></button></div>
      <Suspense fallback={<div className="vector-map query-empty"><LoaderCircle size={22} className="spin" /><span>加载地图</span></div>}><VectorMap key={state.sessionId} bridge={bridge} path={state.project?.projectPath} layers={layers} datasets={datasets} initialView={state.draft?.viewState ?? { center: [114, 27.1], zoom: 5 }} enabled={enabled} selected={selected?.row.id === selectedId && selected?.datasetId === dataset?.id ? selected : null} fitRequest={fitRequest} onViewChange={state.setView} onFailure={state.handleFailure} onSelect={(layerId, id) => { state.setSelectedLayerId(layerId); setSelection({ layerId, featureId: id }); setFitSelected(false); }} /></Suspense>
      <AttributeTable dataset={dataset} page={page} loading={loading} query={query} setQuery={setQuery} selectedId={selectedId} selected={selected?.row.id === selectedId ? selected : null} enabled={enabled} onSelect={(id) => { if (layer) setSelection({ layerId: layer.id, featureId: id }); setFitSelected(true); }} onClear={() => { setSelection(null); setFitSelected(false); }} />
    </div>
    {details && <aside className="data-details-panel" aria-label="数据与检查报告"><DatasetDetails dataset={dataset} /></aside>}
  </div>;
}
