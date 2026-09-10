import { useEffect, useRef, useState } from 'react';
import { FolderOpen, Image, LoaderCircle, Upload } from 'lucide-react';
import type { RasterInspection } from '../../../../shared/contracts';
import { normalizeError, type DesktopBridge } from '../bridge';
import type { WorkspaceState } from '../use-workspace';
import { Modal } from './Modal';
import { RasterMetadata } from './RasterMetadata';

export function ImportRaster({ bridge, state, onClose }: { bridge: DesktopBridge; state: WorkspaceState; onClose: () => void }) {
  const [sourcePath, setSourcePath] = useState('');
  const [inspection, setInspection] = useState<RasterInspection | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const sequence = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const locked = !!state.busy || state.needsReopen;
  useEffect(() => () => { sequence.current += 1; controller.current?.abort(); }, []);
  useEffect(() => { if (state.needsReopen) { sequence.current += 1; controller.current?.abort(); setLoading(false); setInspection(null); } }, [state.needsReopen]);
  const choose = async () => {
    const token = ++sequence.current;
    controller.current?.abort();
    const currentController = new AbortController();
    controller.current = currentController;
    setLoading(true);
    setError('');
    try {
      const path = await bridge.chooseRaster();
      if (token !== sequence.current || !path) return;
      setSourcePath(path);
      setInspection(null);
      const result = await state.inspectRaster(path, currentController.signal);
      if (token === sequence.current) setInspection(result);
    } catch (cause) {
      if (token !== sequence.current) return;
      const failure = normalizeError(cause);
      setError(failure.message);
      if (failure.data?.kind?.startsWith('ENGINE_')) state.handleFailure(cause);
    } finally { if (token === sequence.current) setLoading(false); }
  };
  return <Modal title="导入 GeoTIFF" className="raster-import-modal" onCancel={onClose} busy={locked}>
    <div className="import-picker"><button className="button" disabled={locked} onClick={() => void choose()}><FolderOpen size={16} />选择栅格文件</button><span className="muted small">TIF / TIFF</span></div>
    {sourcePath && <p className="source-location"><code>{sourcePath}</code></p>}
    {loading ? <div className="query-empty" role="status"><LoaderCircle size={19} className="spin" />正在读取栅格元数据</div> : inspection ? <section className="raster-inspection" aria-label="栅格元数据"><dl className="compact-metadata"><div><dt>来源 CRS</dt><dd>{inspection.crsAuthority ?? (inspection.crsWkt ? '自定义 CRS' : '未知')}</dd></div><div><dt>格式</dt><dd>{inspection.driver}</dd></div></dl>{!inspection.crsWkt && <p className="inline-error">来源 CRS 未知；导入后仅保留元数据，不能上图或按位置查询。</p>}<RasterMetadata raster={inspection.raster} />{!!inspection.warnings.length && <ul className="data-warnings">{inspection.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}</section> : <div className="query-empty"><Image size={24} />未选择栅格</div>}
    {(error || state.error) && <div className="inline-error" role="alert">{error || state.error?.message}</div>}
    <div className="modal-actions"><button className="button" disabled={locked} onClick={onClose}>取消</button><button className="button primary" disabled={locked || loading || !inspection || !!state.activeTask} onClick={async () => { if (inspection && await state.importRaster(sourcePath)) onClose(); }}><Upload size={16} />导入栅格</button></div>
  </Modal>;
}
