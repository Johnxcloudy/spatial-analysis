import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Download, Info, LoaderCircle, MapPin, Scan, Table2 } from 'lucide-react';
import type { DesktopBridge } from '../bridge';
import type { WorkspaceState } from '../use-workspace';
import { initialAttributeQuery, useAttributePage, useSelectedFeature } from '../use-vector-data';
import { AttributeTable } from './AttributeTable';
import { DatasetDetails } from './DatasetDetails';
import { GeneratePoints } from './GeneratePoints';
import type { FitRequest } from './VectorMap';

const VectorMap = lazy(() => import('./VectorMap').then((module) => ({ default: module.VectorMap })));

export function VectorWorkspace({ bridge, state, fitRequest, onFit }: { bridge: DesktopBridge; state: WorkspaceState; fitRequest: FitRequest | null; onFit: (bounds: [number, number, number, number]) => void }) {
  const [query, setQuery] = useState(initialAttributeQuery);
  const [selection, setSelection] = useState<{ sourceId: string; featureId: string; sourceRow?: string | null } | null>(null);
  const [details, setDetails] = useState(false);
  const [generating, setGenerating] = useState(false);
  const hiddenFitKey = useRef<number | null>(null);
  const [fitSelected, setFitSelected] = useState(false);
  const layers = state.workspace?.layers ?? [];
  const datasets = state.workspace?.datasets ?? [];
  const layer = layers.find((item) => item.id === state.selectedLayerId);
  const dataset = datasets.find((item) => item.id === (state.selectedTableId ?? layer?.datasetId));
  const table = dataset?.kind === 'table' ? dataset : undefined;
  const vector = dataset?.kind === 'vector' ? dataset : undefined;
  const sourceId = table?.id ?? layer?.id;
  const selectedId = selection?.sourceId === sourceId ? selection?.featureId ?? null : null;
  const enabled = !!state.project && !!state.runtime && !state.needsReopen && state.native;
  useEffect(() => { if (table) hiddenFitKey.current = fitRequest?.key ?? null; }, [table?.id, fitRequest?.key]);
  const mapFitRequest = fitRequest?.key === hiddenFitKey.current ? null : fitRequest;
  useEffect(() => {
    setQuery(initialAttributeQuery);
    setSelection((current) => current?.sourceId === sourceId ? current : null);
    setFitSelected(false);
    setGenerating(false);
  }, [sourceId, dataset?.version]);
  const { page, loading } = useAttributePage(bridge, state.project?.projectPath, dataset, query, enabled, state.handleFailure);
  const { selected } = useSelectedFeature(bridge, state.project?.projectPath, vector, selectedId, enabled, state.handleFailure);
  useEffect(() => { if (fitSelected && selected?.row.id === selectedId && selected?.boundsWgs84) { onFit(selected.boundsWgs84); setFitSelected(false); } }, [selected, selectedId, fitSelected, onFit]);
  return <div className={`vector-workspace ${table ? 'table-workspace' : ''} ${details ? 'details-open' : ''}`}>
    <div className="map-workspace-main"><div className="map-toolbar"><span><Table2 size={15} /><strong>{table?.name ?? layer?.name ?? '地图'}</strong></span>{table ? <button className="icon-button" aria-label="生成点数据" title="生成点数据" disabled={!enabled || !!state.activeTask || !!state.busy} onClick={() => setGenerating(true)}><MapPin size={17} /></button> : <><span className="map-crs-label">EPSG:3857</span><button className="icon-button" aria-label="定位当前图层" title="定位当前图层" disabled={!vector?.boundsWgs84} onClick={() => vector?.boundsWgs84 && onFit(vector.boundsWgs84)}><Scan size={17} /></button></>}<button className="icon-button" aria-label="导出当前数据" title="导出 GeoPackage" disabled={!dataset || !enabled || !!state.activeTask || !!state.busy} onClick={() => { if (dataset) void (dataset.kind === 'table' ? state.exportTable(dataset.id, dataset.name) : state.exportVector(dataset.id, dataset.name)); }}><Download size={17} /></button><button className={`icon-button ${details ? 'pressed' : ''}`} aria-label="数据与检查报告" title="数据与检查报告" aria-pressed={details} onClick={() => setDetails(!details)}><Info size={17} /></button></div>
      {!table && <Suspense fallback={<div className="vector-map query-empty"><LoaderCircle size={22} className="spin" /><span>加载地图</span></div>}><VectorMap key={state.sessionId} bridge={bridge} path={state.project?.projectPath} layers={layers} datasets={datasets.filter((item) => item.kind === 'vector')} initialView={state.draft?.viewState ?? { center: [114, 27.1], zoom: 5 }} enabled={enabled} selected={selected?.row.id === selectedId && selected?.datasetId === vector?.id ? selected : null} fitRequest={mapFitRequest} onViewChange={state.setView} onFailure={state.handleFailure} onSelect={(layerId, id) => { state.setSelectedLayerId(layerId); setSelection({ sourceId: layerId, featureId: id }); setFitSelected(false); }} /></Suspense>}
      <AttributeTable dataset={dataset} page={page} loading={loading} query={query} setQuery={setQuery} selectedId={selectedId} selected={selected?.row.id === selectedId ? selected : null} selectedSourceRow={selection?.sourceId === sourceId ? selection?.sourceRow : undefined} enabled={enabled} onSelect={(id) => { if (sourceId) setSelection({ sourceId, featureId: id, sourceRow: page?.rows.find((row) => row.id === id)?.sourceRow }); setFitSelected(!!vector); }} onClear={() => { setSelection(null); setFitSelected(false); }} />
    </div>
    {details && <aside className="data-details-panel" aria-label="数据与检查报告"><DatasetDetails dataset={dataset} /></aside>}
    {generating && table && <GeneratePoints key={table.id} dataset={table} state={state} onClose={() => setGenerating(false)} />}
  </div>;
}
