import { useRef, useState } from 'react';
import { Database, FileUp, FolderOpen, LoaderCircle, Upload } from 'lucide-react';
import type { SourceInspection } from '../../../../shared/contracts';
import type { DesktopBridge } from '../bridge';
import type { WorkspaceState } from '../use-workspace';
import { normalizeError } from '../bridge';
import { Modal } from './Modal';

export function ImportVector({ bridge, state, onClose }: { bridge: DesktopBridge; state: WorkspaceState; onClose: () => void }) {
  const [inspection, setInspection] = useState<SourceInspection | null>(null);
  const [sourcePath, setSourcePath] = useState('');
  const [sourceLayer, setSourceLayer] = useState('');
  const [encoding, setEncoding] = useState('');
  const [assignedCrs, setAssignedCrs] = useState('');
  const [choosing, setChoosing] = useState(false);
  const [localError, setLocalError] = useState('');
  const sequence = useRef(0);
  const locked = choosing || !!state.busy;
  const layer = inspection?.layers.find((item) => item.name === sourceLayer);
  const missingCrs = !!layer && !layer.crsWkt && !layer.crsAuthority;

  const inspect = async (path: string, selectedEncoding: string) => {
    const token = ++sequence.current;
    setInspection(null);
    setSourceLayer('');
    setAssignedCrs('');
    const result = await state.inspectSource(path, selectedEncoding || null);
    if (token !== sequence.current || !result) return;
    setInspection(result);
    setSourceLayer(result.layers[0]?.name ?? '');
  };
  const choose = async (gdb: boolean) => {
    setChoosing(true);
    setLocalError('');
    try {
      const path = await (gdb ? bridge.chooseGdb() : bridge.chooseVector());
      if (path && !Array.isArray(path)) { setSourcePath(path); await inspect(path, encoding); }
    } catch (cause) { setLocalError(normalizeError(cause).message); }
    finally { setChoosing(false); }
  };
  const submit = async () => {
    if (!inspection || !layer) return;
    const success = await state.importVector({ sourcePath: inspection.sourcePath, sourceLayer: layer.name, encoding: encoding || null, assignedCrs: missingCrs ? assignedCrs.trim() || null : null });
    if (success) onClose();
  };
  return <Modal title="导入矢量数据" onCancel={onClose} busy={locked}>
    <div className="import-picker"><button className="button" disabled={locked} onClick={() => void choose(false)}><FileUp size={17} />选择文件</button><button className="button" disabled={locked} onClick={() => void choose(true)}><FolderOpen size={17} />选择 GDB</button></div>
    {sourcePath && <p className="source-location"><code>{sourcePath}</code></p>}
    <label className="field">字符编码<select value={encoding} disabled={locked} onChange={(event) => { setEncoding(event.target.value); if (sourcePath) void inspect(sourcePath, event.target.value); }}><option value="">自动识别</option><option value="UTF-8">UTF-8</option><option value="GBK">GBK</option><option value="GB18030">GB18030</option></select></label>
    {inspection && <>
      <label className="field">数据层<select aria-label="源数据层" value={sourceLayer} disabled={locked} onChange={(event) => { setSourceLayer(event.target.value); setAssignedCrs(''); }}>{inspection.layers.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}</select></label>
      {!inspection.layers.length && <p className="inline-error">数据源中没有可读取的数据层。</p>}
      {layer && <dl className="compact-metadata"><div><dt>格式</dt><dd>{inspection.driver}</dd></div><div><dt>几何类型</dt><dd>{layer.geometryType ?? '未知'}</dd></div><div><dt>要素数</dt><dd>{layer.featureCount?.toLocaleString('zh-CN') ?? '未读取'}</dd></div><div><dt>字段数</dt><dd>{layer.fields.length}</dd></div><div><dt>来源 CRS</dt><dd>{layer.crsAuthority ?? (layer.crsWkt ? '自定义 CRS' : '未提供')}</dd></div></dl>}
      {missingCrs && <><label className="field">声明来源 CRS<input aria-label="声明来源 CRS" value={assignedCrs} disabled={locked} placeholder="未指定" onChange={(event) => setAssignedCrs(event.target.value)} /></label><p className="import-warning">未声明 CRS 的数据仅提供属性查看。</p></>}
      {!!inspection.warnings.length && <ul className="data-warnings">{inspection.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
    </>}
    {!inspection && locked && <div className="query-empty"><LoaderCircle size={20} className="spin" /><span>正在读取数据源</span></div>}
    {!sourcePath && <div className="import-formats"><Database size={17} /><span>GeoPackage / Shapefile / GeoJSON / File Geodatabase</span></div>}
    {(localError || state.error) && <div role="alert" className="inline-error">{localError || state.error?.message}</div>}
    <div className="modal-actions"><button className="button" disabled={locked} onClick={onClose}>取消</button><button className="button primary" disabled={locked || !layer || !!state.activeTask || state.needsReopen} onClick={() => void submit()}><Upload size={16} />导入所选数据层</button></div>
  </Modal>;
}
