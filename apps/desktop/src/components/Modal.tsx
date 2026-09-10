import { useEffect, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';

export function Modal({ title, children, onCancel, busy = false, className = '' }: {
  title: string;
  children: ReactNode;
  onCancel: () => void;
  busy?: boolean;
  className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  return <dialog ref={ref} className={`modal ${className}`} aria-labelledby="modal-title" onCancel={(event) => {
    event.preventDefault();
    if (!busy) onCancel();
  }}>
    <div className="modal-heading"><h2 id="modal-title">{title}</h2><button className="icon-button" aria-label="取消" title="取消" disabled={busy} onClick={onCancel}><X size={18} /></button></div>
    {children}
  </dialog>;
}
