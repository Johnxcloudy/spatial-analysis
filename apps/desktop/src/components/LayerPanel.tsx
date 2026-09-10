import { useEffect, useState } from 'react';
import { ArrowDown, ArrowUp, Eye, EyeOff, Image, Layers3, Scan, Table2, Trash2, X } from 'lucide-react';
import type { Bounds } from '../../../../shared/contracts';
import type { WorkspaceState } from '../use-workspace';
import { Modal } from './Modal';
import { RasterStyleEditor } from './RasterStyleEditor';
import { VectorStyleEditor } from './VectorStyleEditor';

export function LayerPanel({ state, onFit }: { state: WorkspaceState; onFit: (bounds: Bounds) => void }) {
  const layers = state.workspace?.layers ?? [];
  const tables = state.workspace?.datasets.filter((item) => item.kind === 'table') ?? [];
  const layer = layers.find((item) => item.id === state.selectedLayerId);
  const dataset = state.workspace?.datasets.find((item) => item.id === layer?.datasetId);
  const [name, setName] = useState('');
  const [opacity, setOpacity] = useState(1);
  const [category, setCategory] = useState('');
  const [categoryColor, setCategoryColor] = useState('#5476ab');
  const [removing, setRemoving] = useState(false);
  const disabled = !!state.busy || state.copying || state.needsReopen || !state.runtime;
  useEffect(() => { setName(layer?.name ?? ''); setOpacity(layer?.opacity ?? 1); setCategory(''); }, [layer?.id, layer?.name, layer?.opacity]);
  const reorder = (index: number, amount: number) => {
    const ids = layers.map((item) => item.id);
    [ids[index], ids[index + amount]] = [ids[index + amount], ids[index]];
    void state.reorderLayers(ids);
  };
  const commitOpacity = () => { if (layer && opacity !== layer.opacity) void state.updateLayer(layer.id, { opacity }); };
  return <>
    <div className="layer-list" aria-label="图层列表">{layers.map((item, index) => {
      const data = state.workspace?.datasets.find((entry) => entry.id === item.datasetId);
      return <div key={item.id} className={`layer-row ${item.id === state.selectedLayerId ? 'active' : ''}`}>
        <button className="icon-button layer-eye" title={item.visible ? `隐藏 ${item.name}` : `显示 ${item.name}`} aria-label={item.visible ? `隐藏 ${item.name}` : `显示 ${item.name}`} disabled={disabled} onClick={() => void state.updateLayer(item.id, { visible: !item.visible })}>{item.visible ? <Eye size={16} /> : <EyeOff size={16} />}</button>
        <button className="layer-select" aria-pressed={item.id === state.selectedLayerId} onClick={() => state.setSelectedLayerId(item.id)}>{data?.kind === 'raster' ? <Image size={15} /> : <i style={{ background: item.color }} />}<span><strong>{item.name}</strong><small>{data?.kind === 'raster' ? `${data.raster.width} × ${data.raster.height} · ${data.raster.bandCount} 波段${!data.crsWkt ? ' · CRS 未知' : !data.boundsWgs84 ? ' · 仅元数据' : ''}` : `${data?.geometryType ?? '矢量'} · ${data?.featureCount.toLocaleString('zh-CN') ?? '未知'}${!data?.boundsWgs84 ? ' · 仅属性' : ''}`}</small></span></button>
        <div className="layer-order"><button className="icon-button" aria-label={`上移 ${item.name}`} title="上移图层" disabled={disabled || index === 0} onClick={() => reorder(index, -1)}><ArrowUp size={13} /></button><button className="icon-button" aria-label={`下移 ${item.name}`} title="下移图层" disabled={disabled || index === layers.length - 1} onClick={() => reorder(index, 1)}><ArrowDown size={13} /></button></div>
      </div>;
    })}{!layers.length && <div className="layer-empty"><Layers3 size={27} strokeWidth={1.3} /><span>{state.project ? '暂无图层' : '未打开项目'}</span></div>}</div>
    {!!tables.length && <section className="table-list" aria-label="表格列表"><h2>表格</h2>{tables.map((table) => <button key={table.id} className={`table-select ${state.selectedTableId === table.id ? 'active' : ''}`} aria-pressed={state.selectedTableId === table.id} onClick={() => state.setSelectedTableId(table.id)}><Table2 size={16} /><span><strong>{table.name}</strong><small>{table.featureCount.toLocaleString('zh-CN')} 条记录</small></span></button>)}</section>}
    {layer && <div className="layer-settings"><div className="section-heading"><h2>图层设置</h2><div><button className="icon-button" title="缩放至图层" aria-label="缩放至图层" disabled={!dataset?.boundsWgs84 || state.copying} onClick={() => dataset?.boundsWgs84 && onFit(dataset.boundsWgs84)}><Scan size={17} /></button><button className="icon-button" title="移除图层" aria-label="移除图层" disabled={disabled || !!state.activeTask} onClick={() => setRemoving(true)}><Trash2 size={16} /></button></div></div>
      <fieldset disabled={disabled}><label className="field">图层名称<input value={name} onChange={(event) => setName(event.target.value)} onBlur={() => { if (name.trim() && name.trim() !== layer.name) void state.updateLayer(layer.id, { name: name.trim() }); else setName(layer.name); }} /></label>
        <label className="field">不透明度 <span className="range-field"><input type="range" aria-label="图层不透明度" min={0} max={1} step={0.05} value={opacity} onChange={(event) => setOpacity(Number(event.target.value))} onPointerUp={commitOpacity} onKeyUp={commitOpacity} onBlur={commitOpacity} /><output>{Math.round(opacity * 100)}%</output></span></label>
        {dataset?.kind === 'raster' ? <RasterStyleEditor key={layer.id} dataset={dataset} style={layer.rasterStyle} disabled={disabled} onSave={(rasterStyle) => state.updateLayer(layer.id, { rasterStyle })} /> : <>{!layer.cartography && <><label className="field">渲染<select aria-label="分类渲染字段" value={layer.categoryField ?? ''} onChange={(event) => void state.updateLayer(layer.id, { categoryField: event.target.value || null, categoryColors: {} })}><option value="">单一符号</option>{dataset?.fields.map((field) => <option key={field.name} value={field.name}>{field.alias || field.name}</option>)}</select></label>
        {!layer.categoryField ? <label className="color-field">符号颜色<input type="color" aria-label="图层颜色" value={layer.color} onChange={(event) => void state.updateLayer(layer.id, { color: event.target.value })} /></label> : <div className="category-settings">{Object.entries(layer.categoryColors).map(([key, color]) => <div key={key}><span title={key}>{key === 'null' ? 'NULL' : key}</span><input type="color" aria-label={`类别 ${key} 颜色`} value={color} onChange={(event) => void state.updateLayer(layer.id, { categoryColors: { ...layer.categoryColors, [key]: event.target.value } })} /><button className="icon-button" aria-label={`删除类别 ${key} 颜色`} title="恢复默认颜色" onClick={() => { const colors = { ...layer.categoryColors }; delete colors[key]; void state.updateLayer(layer.id, { categoryColors: colors }); }}><X size={13} /></button></div>)}<form onSubmit={(event) => { event.preventDefault(); if (category !== '') { void state.updateLayer(layer.id, { categoryColors: { ...layer.categoryColors, [category]: categoryColor } }); setCategory(''); } }}><input aria-label="类别值" placeholder="类别值" value={category} onChange={(event) => setCategory(event.target.value)} /><input type="color" aria-label="类别颜色" value={categoryColor} onChange={(event) => setCategoryColor(event.target.value)} /><button className="button compact" disabled={!category}>设置</button></form></div>}
        </>}{dataset?.kind === 'vector' && <VectorStyleEditor key={`${state.workspace?.projectId}:${layer.id}`} dataset={dataset} layer={layer} disabled={disabled} onSave={(changes) => state.updateLayer(layer.id, changes)} />}</>}
      </fieldset>
    </div>}
    {removing && layer && <Modal title="移除图层" busy={!!state.busy} onCancel={() => setRemoving(false)}><p className="modal-description">{layer.name}</p><p className="muted small">托管数据快照将保留。</p><div className="modal-actions"><button className="button" onClick={() => setRemoving(false)}>取消</button><button className="button" disabled={disabled} onClick={async () => { if (await state.removeLayer(layer.id)) setRemoving(false); }}><Trash2 size={15} />移除</button></div></Modal>}
  </>;
}
