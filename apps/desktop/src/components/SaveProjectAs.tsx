import { useState, type FormEvent } from 'react';
import { Copy, FolderOpen, LoaderCircle } from 'lucide-react';
import type { DesktopBridge } from '../bridge';
import { normalizeError } from '../bridge';
import { validateDirectoryName } from '../project-state';
import type { WorkspaceState } from '../use-workspace';
import { Modal } from './Modal';

export function SaveProjectAs({ bridge, state, onClose }: { bridge: DesktopBridge; state: WorkspaceState; onClose: () => void }) {
  const [name, setName] = useState('');
  const [parent, setParent] = useState('');
  const [choosing, setChoosing] = useState(false);
  const [validation, setValidation] = useState('');
  const [attempted, setAttempted] = useState(false);
  const locked = !!state.busy || state.copying || choosing;
  const choose = async () => {
    setChoosing(true);
    setValidation('');
    setAttempted(false);
    try {
      const selected = await bridge.chooseParent();
      if (selected && !Array.isArray(selected)) setParent(selected);
    } catch (cause) { setValidation(normalizeError(cause).message); }
    finally { setChoosing(false); }
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const invalid = validateDirectoryName(name) ?? (!parent ? '请选择项目父目录。' : null);
    setValidation(invalid ?? '');
    setAttempted(!invalid);
    if (!invalid && await state.saveAs(name, parent)) onClose();
  };
  return <Modal title="项目另存为" busy={locked} onCancel={onClose}>
    <form onSubmit={(event) => void submit(event)}>
      <dl className="stacked-metadata save-as-metadata"><div><dt>项目名称</dt><dd>{state.draft?.name}</dd></div><div><dt>当前项目</dt><dd><code>{state.project?.projectPath}</code></dd></div></dl>
      <label className="field">新目录名称<input autoFocus value={name} maxLength={100} disabled={locked} onChange={(event) => { setName(event.target.value); setAttempted(false); setValidation(''); }} /></label>
      <label className="field">父目录<div className="path-picker"><input aria-label="父目录" value={parent} readOnly placeholder="尚未选择" /><button type="button" className="icon-button" aria-label="选择父目录" title="选择父目录" disabled={locked} onClick={() => void choose()}>{choosing ? <LoaderCircle className="spin" size={18} /> : <FolderOpen size={18} />}</button></div></label>
      {(validation || (attempted && state.error)) && <div className="inline-error" role="alert">{validation || state.error?.message}</div>}
      <div className="modal-actions"><button type="button" className="button" disabled={locked} onClick={onClose}>取消</button><button type="submit" className="button primary" disabled={locked}>{state.busy ? <LoaderCircle className="spin" size={16} /> : <Copy size={16} />}另存项目</button></div>
    </form>
  </Modal>;
}
