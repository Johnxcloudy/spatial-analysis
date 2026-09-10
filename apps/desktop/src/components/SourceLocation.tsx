import { useEffect, useState } from 'react';
import { FolderSearch, LoaderCircle, RefreshCw } from 'lucide-react';
import type { Dataset, SourceStatus } from '../../../../shared/contracts';
import { normalizeError, type DesktopBridge } from '../bridge';
import type { WorkspaceState } from '../use-workspace';

const availability: Record<SourceStatus['availability'], string> = { present: '来源存在（当前内容未核对）', missing: '来源缺失', internal: '项目内部来源', unavailable: '来源不可访问' };

export function SourceLocation({ bridge, state, dataset }: { bridge: DesktopBridge; state: WorkspaceState; dataset: Dataset }) {
  const [status, setStatus] = useState<SourceStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const enabled = state.native && !!state.runtime && !state.needsReopen && !!state.project;
  const terminalRelocations = JSON.stringify(state.workspace?.tasks.filter((task) => task.kind === 'relocate' && task.datasetId === dataset.id && task.status !== 'running').map((task) => [task.id, task.status, task.updatedAt]));
  useEffect(() => {
    let cancelled = false;
    setStatus(null);
    setError('');
    setLoading(enabled);
    if (enabled && state.project) {
      void bridge.request('source.status', { path: state.project.projectPath, datasetId: dataset.id }).then((next) => {
        if (cancelled) return;
        if (next.datasetId !== dataset.id || next.originalPath !== dataset.source.path || !Object.hasOwn(availability, next.availability)) throw new Error('来源状态与当前数据集不匹配。');
        setStatus(next);
      }).catch((cause) => {
        if (cancelled) return;
        const failure = normalizeError(cause);
        setError(failure.message);
        if (failure.data?.kind?.startsWith('ENGINE_')) state.handleFailure(cause);
      }).finally(() => { if (!cancelled) setLoading(false); });
    }
    return () => { cancelled = true; };
  }, [bridge, enabled, state.project?.projectPath, state.sessionId, dataset.id, dataset.version, dataset.source.path, revision, terminalRelocations, state.handleFailure]);
  return <section className="source-location" aria-label="来源位置">
    <div className="section-heading"><h3>来源位置</h3><div><button className="icon-button" aria-label="刷新来源状态" title="刷新来源状态" disabled={!enabled || loading || !!state.busy || state.copying} onClick={() => setRevision((value) => value + 1)}>{loading ? <LoaderCircle size={15} className="spin" /> : <RefreshCw size={15} />}</button><button className="icon-button" aria-label="重新定位来源" title="重新定位来源" disabled={!enabled || !!state.busy || !!state.activeTask || state.copying || ['TablePoints', 'SpatialAnalysis'].includes(dataset.source.driver)} onClick={() => void state.relocateSource(dataset)}><FolderSearch size={16} /></button></div></div>
    {loading && <p className="muted small" role="status">正在读取来源状态</p>}
    {error && <p className="inline-error" role="alert">{error}</p>}
    {status && <dl className="stacked-metadata"><div><dt>状态</dt><dd>{availability[status.availability]}</dd></div><div><dt>{status.availability === 'internal' ? '当前项目内来源' : '当前来源路径'}</dt><dd><code>{status.resolvedPath}</code></dd></div><div><dt>已重新定位</dt><dd>{status.relocated ? '是' : '否'}</dd></div><div><dt>上次身份核对（历史记录）</dt><dd>{status.verifiedAt ? new Date(status.verifiedAt).toLocaleString('zh-CN', { hour12: false }) : '无重新定位核对记录'}</dd></div></dl>}
  </section>;
}
