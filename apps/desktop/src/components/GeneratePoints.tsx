import { useState } from 'react';
import { MapPin } from 'lucide-react';
import type { TableDataset } from '../../../../shared/contracts';
import type { WorkspaceState } from '../use-workspace';
import { Modal } from './Modal';

export function GeneratePoints({ dataset, state, onClose }: { dataset: TableDataset; state: WorkspaceState; onClose: () => void }) {
  const [xField, setXField] = useState('');
  const [yField, setYField] = useState('');
  const [declaredCrs, setDeclaredCrs] = useState('');
  const locked = !!state.busy || state.needsReopen || !!state.activeTask;
  const duplicateField = !!xField && xField === yField;
  const ready = !!xField && !!yField && !duplicateField && !!declaredCrs.trim() && !locked;
  return <Modal title="生成点数据" onCancel={onClose} busy={!!state.busy}>
    <p className="modal-description">{dataset.name} · {dataset.featureCount.toLocaleString('zh-CN')} 条记录</p>
    <label className="field">X：经度 / 东坐标<select aria-label="X 字段" value={xField} disabled={locked} onChange={(event) => setXField(event.target.value)}><option value="">请选择字段</option>{dataset.fields.map((field) => <option key={field.name} value={field.name}>{field.alias || field.name}</option>)}</select></label>
    <label className="field">Y：纬度 / 北坐标<select aria-label="Y 字段" value={yField} disabled={locked} onChange={(event) => setYField(event.target.value)}><option value="">请选择字段</option>{dataset.fields.map((field) => <option key={field.name} value={field.name}>{field.alias || field.name}</option>)}</select></label>
    <label className="field">来源 CRS<input aria-label="点数据来源 CRS" value={declaredCrs} disabled={locked} placeholder="未指定" spellCheck={false} onChange={(event) => setDeclaredCrs(event.target.value)} /></label>
    {duplicateField && <p className="inline-error" role="alert">X 与 Y 必须选择不同字段。</p>}
    <p className="import-data-policy">无效坐标保留为无几何记录，至少需要一个有效点。原表格保留。</p>
    {state.error && <div className="inline-error" role="alert">{state.error.message}</div>}
    <div className="modal-actions"><button className="button" disabled={!!state.busy} onClick={onClose}>取消</button><button className="button primary" disabled={!ready} onClick={async () => { if (ready && await state.generatePoints(dataset.id, xField, yField, declaredCrs.trim())) onClose(); }}><MapPin size={16} />生成点图层</button></div>
  </Modal>;
}
