import { lazy, Suspense, useCallback, useState, type ReactNode } from 'react';
import { AlertCircle, ArrowRight, Check, CheckCircle2, ChevronRight, Circle, Copy, Database, File, FilePlus2, FileSpreadsheet, FileUp, FolderOpen, Layers3, LoaderCircle, Map as MapIcon, Play, RefreshCw, Save, Server, X } from 'lucide-react';
import type { ProbeReport, RuntimeInfo } from '../../../shared/contracts';
import { desktop, type DesktopBridge } from './bridge';
import { useWorkspace } from './use-workspace';
import { CreateProject } from './components/CreateProject';
import { SaveProjectAs } from './components/SaveProjectAs';
import { Modal } from './components/Modal';
import { ImportVector } from './components/ImportVector';
import { ImportTable } from './components/ImportTable';
import { ImportRaster } from './components/ImportRaster';
import { LayerPanel } from './components/LayerPanel';
import { TaskStrip } from './components/TaskStrip';
import { VectorWorkspace } from './components/VectorWorkspace';
import { AnalysisWorkspace } from './components/AnalysisWorkspace';
import type { FitRequest } from './components/VectorMap';

const ProjectionPreview = lazy(() => import('./components/ProjectionPreview').then((module) => ({ default: module.ProjectionPreview })));

const formatNumber = (value: number) => new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 8 }).format(value);
const formatTime = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString('zh-CN', { hour12: false });
};

function IconButton({ label, children, onClick, disabled }: { label: string; children: ReactNode; onClick: () => void; disabled?: boolean }) {
  return <button className="icon-button" aria-label={label} title={label} onClick={onClick} disabled={disabled}>{children}</button>;
}

function RuntimePanel({ runtime }: { runtime: RuntimeInfo | null }) {
  return <section className="runtime-section">
    <div className="section-heading"><h2>运行环境</h2>{runtime && <span className="muted small">{runtime.packaged ? '内置引擎' : '开发引擎'}</span>}</div>
    {runtime ? <>
      <dl className="runtime-grid"><div><dt>Engine</dt><dd>{runtime.engineVersion}</dd></div><div><dt>Python</dt><dd>{runtime.pythonVersion}</dd></div>{Object.entries(runtime.versions).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>
      <div className="driver-heading">格式驱动</div>
      <dl className="driver-grid">{Object.entries(runtime.drivers).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>
      <div className="file-location"><span>引擎日志</span><code>{runtime.logPath}</code></div>
    </> : <p className="empty-inline">尚未获取运行环境</p>}
  </section>;
}

function DiagnosticReport({ report }: { report: ProbeReport | null }) {
  if (!report) return <div className="empty-checks"><Database size={28} strokeWidth={1.4} /><p>尚无诊断记录</p></div>;
  return <>
    <div className="metric-grid">
      <div><span>矩形面积</span><strong>{formatNumber(report.measuredAreaM2)} <small>m²</small></strong><span>期望 {formatNumber(report.expectedAreaM2)} m²</span></div>
      <div><span>相交面积</span><strong>{formatNumber(report.intersectionAreaM2)} <small>m²</small></strong><span>期望 {formatNumber(report.expectedIntersectionAreaM2)} m²</span></div>
      <div><span>往返转换误差</span><strong>{report.roundTripErrorM < 0.000001 && report.roundTripErrorM !== 0 ? report.roundTripErrorM.toExponential(3) : formatNumber(report.roundTripErrorM)} <small>m</small></strong><span>面积误差 {formatNumber(report.areaErrorM2)} m²</span></div>
    </div>
    <div className="checks-list" aria-label="诊断检查结果">{report.checks.map((check) => <div className="check-row" key={check.id}>{check.passed ? <CheckCircle2 className="success" size={17} /> : <AlertCircle className="danger" size={17} />}<div><strong>{check.label}</strong><p>{check.detail}</p></div><span className={check.passed ? 'check-state success' : 'check-state danger'}>{check.passed ? '通过' : '未通过'}</span></div>)}</div>
    <div className="report-footer"><span>合成样本验证，不代表实际测绘精度。</span><span>{formatNumber(report.durationMs)} ms</span></div>
    <div className="file-location"><span>诊断报告</span><code>{report.reportPath}</code></div>
    <div className="file-location"><span>GeoPackage</span><code>{report.geopackagePath}</code></div>
    <div className="file-location"><span>GeoTIFF</span><code>{report.geotiffPath}</code></div>
  </>;
}

export default function App({ bridge = desktop }: { bridge?: DesktopBridge }) {
  const state = useWorkspace(bridge);
  const [tab, setTab] = useState<'map' | 'analysis' | 'diagnostics' | 'projection'>('map');
  const [sideTab, setSideTab] = useState<'layers' | 'project'>('layers');
  const [importing, setImporting] = useState(false);
  const [importingTable, setImportingTable] = useState(false);
  const [importingRaster, setImportingRaster] = useState(false);
  const [savingAs, setSavingAs] = useState(false);
  const [fitRequest, setFitRequest] = useState<(FitRequest & { sessionId: number }) | null>(null);
  const fit = useCallback((bounds: [number, number, number, number]) => {
    if (state.copying) return;
    setTab('map');
    setFitRequest((previous) => ({ key: (previous?.key ?? 0) + 1, bounds, sessionId: state.sessionId }));
  }, [state.sessionId, state.copying]);
  const locked = !!state.busy || state.copying;
  const ready = state.native && !!state.runtime;
  const connection = !state.native ? '浏览器模式' : state.busy === '连接引擎' ? '连接中' : state.runtime ? '引擎已连接' : '引擎未连接';

  return <div className="app-shell">
    <header className="app-header">
      <div className="brand"><Layers3 size={25} strokeWidth={1.6} /><div><strong>Spatial Analysis</strong><span>Desktop</span></div></div>
      <div className="header-project"><ChevronRight size={16} /><span>{state.project?.name ?? '未打开项目'}</span>{state.dirty && <i className="dirty-dot" title="未保存更改" aria-label="未保存更改" />}</div>
      <div className="connection"><i className={`status-dot ${state.runtime ? 'online' : ''}`} /><span>{connection}</span><IconButton label="重新连接引擎" disabled={!state.native || locked} onClick={() => void state.connect()}><RefreshCw size={16} className={state.busy === '连接引擎' ? 'spin' : ''} /></IconButton></div>
    </header>

    <div className="toolbar" aria-label="项目工具栏">
      <div className="toolbar-group"><button className="button" disabled={!ready || locked} onClick={() => state.requestAction('new')}><FilePlus2 size={16} />新建项目</button><button className="button" disabled={!ready || locked} onClick={() => state.requestAction('open')}><FolderOpen size={16} />打开项目</button><span className="toolbar-divider" /><IconButton label="保存项目" disabled={!ready || !state.project || !state.dirty || state.needsReopen || locked} onClick={() => void state.save()}><Save size={18} /></IconButton><IconButton label="项目另存为" disabled={!ready || !state.project || state.needsReopen || locked || !!state.activeTask} onClick={() => setSavingAs(true)}><Copy size={18} /></IconButton><IconButton label="关闭项目" disabled={!ready || !state.project || locked} onClick={() => state.requestAction('close')}><X size={18} /></IconButton><span className="toolbar-divider" /><button className="button primary" disabled={!ready || !state.project || state.needsReopen || locked || !!state.activeTask} onClick={() => setImporting(true)}><FileUp size={16} />导入数据</button><IconButton label="刷新工作区" disabled={!ready || !state.project || state.needsReopen || locked} onClick={() => void state.refreshWorkspace()}><RefreshCw size={16} /></IconButton></div>
      <button className="button" disabled={!ready || !state.project || state.needsReopen || locked || !!state.activeTask} onClick={() => setImportingTable(true)}><FileSpreadsheet size={16} />导入表格</button>
      <button className="button" disabled={!ready || !state.project || state.needsReopen || locked || !!state.activeTask} onClick={() => setImportingRaster(true)}><Layers3 size={16} />导入栅格</button>
      <span className="toolbar-context">{state.workspace ? `${state.workspace.datasets.length} 个数据集 · ${state.workspace.layers.length} 个图层` : '本地工作区'}</span>
    </div>

    {!state.native && <div className="environment-banner" role="status"><Server size={17} /><span>浏览器模式下本地引擎不可用。项目存取和 GIS 验证需在桌面应用中运行。</span></div>}
    {state.needsReopen && <div className="environment-banner" role="status"><AlertCircle size={17} /><span>引擎会话已中断，当前项目需要重新打开。编辑内容仍保留在表单中。</span></div>}
    {state.error && !state.newProject && !state.pending && !importing && !importingTable && !importingRaster && !savingAs && <div className="error-banner" role="alert"><AlertCircle size={18} /><div><strong>{state.error.message}</strong>{state.error.data?.detail && <p>{state.error.data.detail}</p>}<span className="error-code">{state.error.data?.kind ?? 'ENGINE_ERROR'} · {state.error.code}</span></div></div>}

    <div className="workbench">
      <aside className="project-panel" aria-label="图层与项目">
        <div className="side-tabs" role="tablist" aria-label="侧栏"><button role="tab" aria-selected={sideTab === 'layers'} onClick={() => setSideTab('layers')}><Layers3 size={15} />图层</button><button role="tab" aria-selected={sideTab === 'project'} onClick={() => setSideTab('project')}><File size={15} />项目{state.dirty && <i className="dirty-dot" />}</button></div>
        {sideTab === 'layers' ? <LayerPanel state={state} onFit={fit} /> : <><div className="panel-heading"><span>项目属性</span><span className={state.dirty ? 'save-state dirty' : 'save-state'}>{state.project ? state.dirty ? '未保存' : '已保存' : '未打开'}</span></div>
        {state.project && state.draft ? <div className="project-content">
          <fieldset disabled={locked}>
            <label className="field">项目名称<input value={state.draft.name} onChange={(event) => state.setDraft({ ...state.draft!, name: event.target.value })} /></label>
            <label className="field">项目描述<textarea value={state.draft.description} rows={4} onChange={(event) => state.setDraft({ ...state.draft!, description: event.target.value })} /></label>
            <label className="field">分析 CRS<input value={state.draft.analysisCrs} placeholder="未指定" spellCheck={false} onChange={(event) => state.setDraft({ ...state.draft!, analysisCrs: event.target.value })} /></label>
          </fieldset>
          <dl className="project-meta"><div><dt>显示 CRS</dt><dd>{state.project.displayCrs}</dd></div><div><dt>项目格式</dt><dd>v{state.project.schemaVersion}</dd></div><div><dt>创建时间</dt><dd>{formatTime(state.project.createdAt)}</dd></div><div><dt>上次保存</dt><dd>{formatTime(state.project.updatedAt)}</dd></div></dl>
          <div className="project-path"><span>项目文件</span><code>{state.project.projectPath}</code></div>
        </div> : <div className="project-empty"><FolderOpen size={34} strokeWidth={1.25} /><strong>未打开项目</strong><div><button className="button compact" disabled={!ready || locked} onClick={() => state.requestAction('new')}><FilePlus2 size={15} />新建</button><button className="button compact" disabled={!ready || locked} onClick={() => state.requestAction('open')}><FolderOpen size={15} />打开</button></div></div>}</>}
        <div className="project-panel-footer"><Database size={14} /><span>本地项目存储</span></div>
      </aside>

      <main className="workspace-main">
        <div className="workspace-tabs" role="tablist" aria-label="工作区视图"><button role="tab" id="map-tab" aria-controls="map-panel" aria-selected={tab === 'map'} onClick={() => setTab('map')}><MapIcon size={16} />地图工作区</button><button role="tab" id="analysis-tab" aria-controls="analysis-panel" aria-selected={tab === 'analysis'} onClick={() => setTab('analysis')}><Layers3 size={16} />用地分析</button><button role="tab" id="diagnostics-tab" aria-controls="diagnostics-panel" aria-selected={tab === 'diagnostics'} onClick={() => setTab('diagnostics')}><Server size={16} />运行诊断</button><button role="tab" id="projection-tab" aria-controls="projection-panel" aria-selected={tab === 'projection'} onClick={() => setTab('projection')}><Layers3 size={16} />投影验证</button></div>
        <TaskStrip tasks={state.workspace?.tasks ?? []} disabled={!!state.busy || state.needsReopen} onCancel={(id) => void state.cancelTask(id)} />
        <div hidden={tab !== 'map'} role="tabpanel" id="map-panel" aria-labelledby="map-tab"><VectorWorkspace key={state.sessionId} bridge={bridge} state={state} visible={tab === 'map'} fitRequest={fitRequest?.sessionId === state.sessionId ? fitRequest : null} onFit={fit} /></div>
        <div hidden={tab !== 'analysis'} role="tabpanel" id="analysis-panel" aria-labelledby="analysis-tab"><AnalysisWorkspace key={state.sessionId} bridge={bridge} state={state} visible={tab === 'analysis'} onShowMap={() => setTab('map')} /></div>
        {(tab === 'diagnostics' || tab === 'projection') && <div className="workspace-content">
          <div className="workspace-heading"><h1>{tab === 'diagnostics' ? '本地 GIS 引擎' : '投影验证'}</h1><button className="button primary" disabled={!ready || locked || !!state.activeTask} onClick={() => void state.diagnose()}>{state.busy === '运行 GIS 验证' ? <LoaderCircle size={16} className="spin" /> : <Play size={16} />}运行验证</button></div>
          {tab === 'diagnostics' ? <div role="tabpanel" id="diagnostics-panel" aria-labelledby="diagnostics-tab">
            <section className="diagnostic-section"><div className="section-heading"><h2>GIS 诊断</h2><span className={`diagnostic-state ${state.report ? state.report.ok ? 'success' : 'danger' : ''}`}>{state.busy === '运行 GIS 验证' ? <><LoaderCircle size={14} className="spin" />正在运行</> : state.report ? <>{state.report.ok ? <Check size={14} /> : <AlertCircle size={14} />}{state.report.ok ? '全部通过' : '存在失败项'}</> : <><Circle size={12} />未运行</>}</span></div><DiagnosticReport report={state.report} />{state.report && <button className="text-button" onClick={() => setTab('projection')}>查看投影验证<ArrowRight size={15} /></button>}</section>
            <RuntimePanel runtime={state.runtime} />
          </div> : <div role="tabpanel" id="projection-panel" aria-labelledby="projection-tab"><Suspense fallback={<div className="empty-preview" role="status"><LoaderCircle className="spin" size={24} /><span>正在加载投影视图</span></div>}><ProjectionPreview report={state.report} /></Suspense>{state.report && <p className="projection-disclaimer">合成样本验证，不代表实际测绘精度。</p>}</div>}
        </div>}
      </main>
    </div>

    <footer className="statusbar"><span className="status-message" role="status" aria-live="polite">{state.busy ? <><LoaderCircle className="spin" size={13} />{state.busy}</> : <><span className={`status-dot ${state.runtime ? 'online' : ''}`} />{state.notice || (state.runtime ? '就绪' : state.native ? '等待引擎连接' : '本地引擎不可用')}</>}</span><span>分析 CRS · {state.draft?.analysisCrs || '未指定'}</span><span className="version-label">0.6.0</span></footer>

    {importing && <ImportVector bridge={bridge} state={state} onClose={() => setImporting(false)} />}
    {importingTable && <ImportTable key={state.sessionId} bridge={bridge} state={state} onClose={() => setImportingTable(false)} />}
    {importingRaster && <ImportRaster key={state.sessionId} bridge={bridge} state={state} onClose={() => setImportingRaster(false)} />}
    {state.newProject && <CreateProject bridge={bridge} busy={locked} error={state.error} onCancel={() => state.setNewProject(false)} onCreate={state.create} />}
    {savingAs && <SaveProjectAs key={state.sessionId} bridge={bridge} state={state} onClose={() => setSavingAs(false)} />}
    {state.pending && <Modal title="项目有未保存的更改" onCancel={() => void state.resolvePending('cancel')} busy={locked}><p className="modal-description">{state.project?.name}</p>{state.needsReopen && <p className="modal-description">引擎会话已中断，当前更改无法保存。</p>}{state.error && <div className="inline-error" role="alert">{state.error.message}</div>}<div className="modal-actions unsaved-actions"><button className="button" disabled={locked} onClick={() => void state.resolvePending('cancel')}>取消</button><button className="button" disabled={locked} onClick={() => void state.resolvePending('discard')}>放弃更改</button><button className="button primary" disabled={locked || state.needsReopen} onClick={() => void state.resolvePending('save')}>{locked ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}保存并继续</button></div></Modal>}
  </div>;
}
