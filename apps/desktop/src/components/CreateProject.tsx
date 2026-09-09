import { useState, type FormEvent } from 'react';
import { FolderOpen, FolderPlus, LoaderCircle } from 'lucide-react';
import type { DesktopBridge } from '../bridge';
import { normalizeError } from '../bridge';
import { validateDirectoryName } from '../project-state';
import { Modal } from './Modal';
import type { EngineError } from '../../../../shared/contracts';

export function CreateProject({ bridge, busy, error, onCancel, onCreate }: {
  bridge: DesktopBridge;
  busy: boolean;
  error: EngineError | null;
  onCancel: () => void;
  onCreate: (name: string, parent: string) => Promise<boolean>;
}) {
  const [name, setName] = useState('');
  const [parent, setParent] = useState('');
  const [choosing, setChoosing] = useState(false);
  const [validation, setValidation] = useState('');
  const locked = busy || choosing;
  const choose = async () => {
    setChoosing(true);
    setValidation('');
    try {
      const selected = await bridge.chooseParent();
      if (selected && !Array.isArray(selected)) setParent(selected);
    } catch (cause) {
      setValidation(normalizeError(cause).message);
    } finally { setChoosing(false); }
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const invalid = validateDirectoryName(name) ?? (!parent ? '请选择项目父目录。' : null);
    setValidation(invalid ?? '');
    if (!invalid) void onCreate(name, parent);
  };
  return <Modal title="创建项目" onCancel={onCancel} busy={locked}>
    <form onSubmit={submit}>
      <label className="field">项目名称<input autoFocus value={name} maxLength={100} disabled={locked} onChange={(event) => setName(event.target.value)} /></label>
      <label className="field">父目录<div className="path-picker"><input aria-label="父目录" value={parent} readOnly placeholder="尚未选择" /><button type="button" className="icon-button" title="选择父目录" aria-label="选择父目录" disabled={locked} onClick={() => void choose()}>{choosing ? <LoaderCircle className="spin" size={18} /> : <FolderOpen size={18} />}</button></div></label>
      {(validation || error) && <div className="inline-error" role="alert">{validation || error?.message}</div>}
      <div className="modal-actions"><button type="button" className="button" onClick={onCancel} disabled={locked}>取消</button><button type="submit" className="button primary" disabled={locked}>{busy ? <LoaderCircle className="spin" size={16} /> : <FolderPlus size={16} />}创建项目</button></div>
    </form>
  </Modal>;
}
