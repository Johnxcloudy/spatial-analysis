import { useCallback, useEffect, useRef, useState, type SetStateAction } from 'react';
import { PROTOCOL_VERSION, type AnalysisOptions, type Dataset, type EngineError, type MapLayer, type ProbeReport, type Project, type RuntimeInfo, type SourceInspection, type TableOptions, type Task, type ViewState, type Workspace } from '../../../shared/contracts';
import { normalizeError, type DesktopBridge } from './bridge';
import { draftFromProject, hasUnsavedChanges, validateDirectoryName, type ProjectDraft } from './project-state';

export type ProjectAction = 'new' | 'open' | 'close' | 'exit';
interface CopyIntent { taskId: string; token: number; projectId: string; destination: string }
const samePath = (left: string, right: string) => left.replaceAll('\\', '/').toLowerCase() === right.replaceAll('\\', '/').toLowerCase();

export function useWorkspace(bridge: DesktopBridge) {
  const [project, setProject] = useState<Project | null>(null);
  const [draft, updateDraft] = useState<ProjectDraft | null>(null);
  const [runtime, setRuntime] = useState<RuntimeInfo | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [sessionId, setSessionId] = useState(0);
  const [pollRevision, setPollRevision] = useState(0);
  const [selectedSource, setSelectedSource] = useState<{ kind: 'layer' | 'table'; id: string } | null>(null);
  const selectedLayerId = selectedSource?.kind === 'layer' ? selectedSource.id : null;
  const selectedTableId = selectedSource?.kind === 'table' ? selectedSource.id : null;
  const setSelectedLayerId = useCallback((id: string | null) => setSelectedSource(id ? { kind: 'layer', id } : null), []);
  const setSelectedTableId = useCallback((id: string) => setSelectedSource({ kind: 'table', id }), []);
  const [report, setReport] = useState<ProbeReport | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<EngineError | null>(null);
  const [notice, setNotice] = useState('');
  const [newProject, setNewProject] = useState(false);
  const [pending, setPending] = useState<ProjectAction | null>(null);
  const [needsReopen, setNeedsReopen] = useState(false);
  const [copyIntent, setCopyIntent] = useState<CopyIntent | null>(null);
  const openingCopy = useRef<string | null>(null);
  const copyingRef = useRef(false);
  const [native] = useState(() => bridge.available());
  const inFlight = useRef(false);
  const projectRef = useRef<Project | null>(null);
  const recoveryRef = useRef(false);
  const generation = useRef(0);
  projectRef.current = project;
  const dirty = hasUnsavedChanges(project, draft);
  const activeTask = workspace?.tasks.find((task) => task.status === 'running') ?? null;
  const copying = !needsReopen && (!!copyIntent || activeTask?.kind === 'save_as');
  copyingRef.current = copying;
  const setDraft = useCallback((value: SetStateAction<ProjectDraft | null>) => {
    if (!copyingRef.current) updateDraft(value);
  }, []);

  const handleFailure = useCallback((cause: unknown) => {
    const failure = normalizeError(cause);
    setError(failure);
    if (failure.data?.kind?.startsWith('ENGINE_')) {
      setRuntime(null);
      generation.current += 1;
      setCopyIntent(null);
      copyingRef.current = false;
      if (projectRef.current) {
        recoveryRef.current = true;
        setNeedsReopen(true);
      }
    }
  }, []);

  const run = useCallback(async (label: string, action: () => Promise<void>, duringCopy = false): Promise<boolean> => {
    if (inFlight.current || (copyingRef.current && !duringCopy)) return false;
    inFlight.current = true;
    setBusy(label);
    setError(null);
    setNotice('');
    try {
      await action();
      return true;
    } catch (cause) {
      handleFailure(cause);
      return false;
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  }, [handleFailure]);

  const activeProject = useCallback(() => {
    if (!native) throw new Error('浏览器模式下本地引擎不可用。');
    if (!projectRef.current) throw new Error('当前没有打开的项目。');
    if (recoveryRef.current) throw new Error('引擎会话已中断，请重新打开项目。');
    return projectRef.current;
  }, [native]);

  const loadWorkspace = useCallback(async (current: Project, token: number, preferredDatasetId?: string | null) => {
    const next = await bridge.request('workspace.get', { path: current.projectPath });
    if (generation.current !== token || projectRef.current?.id !== current.id) return;
    if (next.projectId !== current.id) throw new Error('工作区响应与当前项目不匹配。');
    setWorkspace(next);
    setSelectedSource((selected) => {
      if (preferredDatasetId) {
        const preferredLayer = next.layers.find((layer) => layer.datasetId === preferredDatasetId);
        if (preferredLayer) return { kind: 'layer', id: preferredLayer.id };
        if (next.datasets.some((dataset) => dataset.kind === 'table' && dataset.id === preferredDatasetId)) return { kind: 'table', id: preferredDatasetId };
      }
      if (selected?.kind === 'layer' && next.layers.some((layer) => layer.id === selected.id)) return selected;
      if (selected?.kind === 'table' && next.datasets.some((dataset) => dataset.kind === 'table' && dataset.id === selected.id)) return selected;
      const firstTable = next.datasets.find((dataset) => dataset.kind === 'table');
      return next.layers[0] ? { kind: 'layer', id: next.layers[0].id } : firstTable ? { kind: 'table', id: firstTable.id } : null;
    });
  }, [bridge]);

  const refreshWorkspace = useCallback(() => run('刷新工作区', async () => {
    await loadWorkspace(activeProject(), generation.current);
    setPollRevision((value) => value + 1);
  }), [run, loadWorkspace, activeProject]);

  const acceptProject = useCallback(async (next: Project) => {
    const token = generation.current;
    let nextWorkspace: Workspace;
    try {
      nextWorkspace = await bridge.request('workspace.get', { path: next.projectPath });
      if (nextWorkspace.projectId !== next.id) throw new Error('工作区响应与新项目不匹配。');
    } catch (cause) {
      if (generation.current === token && projectRef.current) {
        generation.current += 1;
        recoveryRef.current = true;
        setNeedsReopen(true);
      }
      throw cause;
    }
    if (generation.current !== token) return;
    generation.current += 1;
    setSessionId(generation.current);
    projectRef.current = next;
    setProject(next);
    updateDraft(draftFromProject(next));
    setCopyIntent(null);
    copyingRef.current = false;
    setReport(null);
    recoveryRef.current = false;
    setNeedsReopen(false);
    setWorkspace(nextWorkspace);
    const firstLayer = nextWorkspace.layers[0];
    const firstTable = nextWorkspace.datasets.find((dataset) => dataset.kind === 'table');
    setSelectedSource(firstLayer ? { kind: 'layer', id: firstLayer.id } : firstTable ? { kind: 'table', id: firstTable.id } : null);
  }, [bridge]);

  const connect = useCallback(() => run('连接引擎', async () => {
    setRuntime(null);
    const info = await bridge.request('runtime.info');
    if (info.protocolVersion !== PROTOCOL_VERSION) throw new Error('引擎协议版本不兼容，请检查桌面应用和引擎版本。');
    setRuntime(info);
    setNotice(recoveryRef.current ? '引擎已重连，当前项目需要重新打开' : '本地引擎已连接');
  }), [bridge, run]);

  useEffect(() => { if (native) void connect(); }, [native, connect]);

  const save = () => run('保存项目', async () => {
    if (!project || !draft) throw new Error('当前没有打开的项目。');
    if (recoveryRef.current) throw new Error('引擎会话已中断，请重新打开项目后再保存。当前编辑内容仍保留在表单中。');
    if (!draft.name.trim()) throw new Error('项目名称不能为空。');
    const next = await bridge.request('project.save', {
      path: project.projectPath,
      name: draft.name.trim(),
      description: draft.description,
      analysisCrs: draft.analysisCrs.trim() || null,
      displayCrs: 'EPSG:3857',
      viewState: draft.viewState,
    });
    setProject(next);
    setDraft(draftFromProject(next));
    setNotice('项目已保存');
  });

  const executeAction = async (action: ProjectAction) => {
    if (action === 'new') { setError(null); setNewProject(true); return; }
    if (action === 'open') {
      await run('打开项目', async () => {
        const path = await bridge.chooseProject();
        if (!path || Array.isArray(path)) return;
        await acceptProject(await bridge.request('project.open', { path }));
        setNotice('项目已打开');
      });
      return;
    }
    if (action === 'close') {
      await run('关闭项目', async () => {
        await bridge.request('project.close');
        generation.current += 1;
        setSessionId(generation.current);
        projectRef.current = null;
        setProject(null);
        updateDraft(null);
        setCopyIntent(null);
        setReport(null);
        recoveryRef.current = false;
        setNeedsReopen(false);
        setWorkspace(null);
        setSelectedLayerId(null);
        setNotice('项目已关闭');
      });
      return;
    }
    await run('退出应用', async () => {
      try {
        if (project) await bridge.request('project.close');
      } finally {
        await bridge.closeWindow();
      }
    });
  };

  const requestAction = (action: ProjectAction) => {
    if (inFlight.current || copyingRef.current) return;
    if (dirty) { setPending(action); return; }
    void executeAction(action);
  };

  const closeState = useRef({ busy, dirty, copying, requestAction });
  closeState.current = { busy, dirty, copying, requestAction };
  useEffect(() => {
    if (!native) return;
    let disposed = false;
    let unlisten: (() => void) | undefined;
    void bridge.onClose((preventDefault) => {
      if (closeState.current.busy || closeState.current.dirty || closeState.current.copying) {
        preventDefault();
        if (!closeState.current.busy && !closeState.current.copying) closeState.current.requestAction('exit');
      }
    }).then((cleanup) => { if (disposed) cleanup(); else unlisten = cleanup; }).catch((cause) => setError(normalizeError(cause)));
    return () => { disposed = true; unlisten?.(); };
  }, [native, bridge]);

  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {
      if (dirty || busy || copying) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [dirty, busy, copying]);

  const resolvePending = async (choice: 'save' | 'discard' | 'cancel') => {
    if (choice === 'cancel') { setPending(null); return; }
    const action = pending;
    if (!action) return;
    if (choice === 'save' && !await save()) return;
    setPending(null);
    await executeAction(action);
  };

  const create = (name: string, parent: string) => run('创建项目', async () => {
    const invalid = validateDirectoryName(name);
    if (invalid) throw new Error(invalid);
    if (!parent) throw new Error('请选择项目父目录。');
    const directory = await bridge.join(parent, name);
    await acceptProject(await bridge.request('project.create', { directory, name }));
    setNewProject(false);
    setNotice('项目已创建');
  });

  const diagnose = () => run('运行 GIS 验证', async () => {
    setReport(null);
    const directory = await bridge.diagnosticDirectory(project?.projectPath);
    const next = await bridge.request('diagnostics.run', { directory });
    setReport(next);
    setNotice(next.ok ? '诊断检查已通过' : '诊断完成，存在未通过项');
  });

  const setView = useCallback((viewState: ViewState) => {
    if (!copyingRef.current) updateDraft((current) => current ? { ...current, viewState } : current);
  }, []);

  const inspectSource = async (sourcePath: string, encoding: string | null): Promise<SourceInspection | null> => {
    let result: SourceInspection | null = null;
    await run('检查数据源', async () => {
      activeProject();
      result = await bridge.request('source.inspect', { sourcePath, encoding });
    });
    return result;
  };

  const rememberTask = useCallback((task: Task) => {
    setWorkspace((current) => current ? { ...current, tasks: [task, ...current.tasks.filter((item) => item.id !== task.id)] } : current);
  }, []);

  const saveAs = (name: string, parent: string) => run('开始另存项目', async () => {
    const current = activeProject();
    if (!draft || !draft.name.trim()) throw new Error('项目名称不能为空。');
    if (activeTask) throw new Error('请等待当前任务完成。');
    const invalid = validateDirectoryName(name);
    if (invalid) throw new Error(invalid);
    if (!parent) throw new Error('请选择项目父目录。');
    const token = generation.current;
    const directory = await bridge.join(parent, name);
    const destination = await bridge.join(directory, 'project.spa');
    const task = await bridge.request('project.saveAs', { path: current.projectPath, directory, name: draft.name.trim(), description: draft.description, analysisCrs: draft.analysisCrs.trim() || null, displayCrs: current.displayCrs, viewState: draft.viewState });
    if (generation.current !== token || projectRef.current?.id !== current.id) return;
    if (task.kind !== 'save_as' || task.datasetId !== null || !task.destination || !samePath(task.destination, destination)) throw new Error('另存任务响应与目标项目不匹配。');
    copyingRef.current = true;
    setCopyIntent({ taskId: task.id, token, projectId: current.id, destination });
    rememberTask(task);
    setNotice('项目另存任务已开始');
  });

  const relocateSource = (dataset: Dataset) => run('重新定位来源', async () => {
    const current = activeProject();
    if (activeTask) throw new Error('请等待当前任务完成。');
    if (['TablePoints', 'SpatialAnalysis'].includes(dataset.source.driver)) throw new Error('派生数据使用项目内的输入快照。');
    const token = generation.current;
    const sourcePath = await bridge.chooseSource(dataset);
    if (!sourcePath || Array.isArray(sourcePath) || generation.current !== token) return;
    const task = await bridge.request('source.relocate', { path: current.projectPath, datasetId: dataset.id, sourcePath });
    if (generation.current !== token || projectRef.current?.id !== current.id) return;
    rememberTask(task);
    setNotice('来源身份核对已开始');
  });

  const importVector = (params: { sourcePath: string; sourceLayer: string; encoding: string | null; assignedCrs: string | null }) => run('开始导入', async () => {
    const current = activeProject();
    const task = await bridge.request('vector.import', { path: current.projectPath, ...params });
    rememberTask(task);
    setNotice('导入任务已开始');
  });

  const inspectTable = useCallback((options: TableOptions, signal?: AbortSignal) => {
    activeProject();
    return bridge.request('table.inspect', { ...options }, signal);
  }, [activeProject, bridge]);

  const inspectRaster = useCallback((sourcePath: string, signal?: AbortSignal) => {
    activeProject();
    return bridge.request('raster.inspect', { sourcePath }, signal);
  }, [activeProject, bridge]);

  const importRaster = (sourcePath: string) => run('开始导入栅格', async () => {
    const current = activeProject();
    rememberTask(await bridge.request('raster.import', { path: current.projectPath, sourcePath }));
    setNotice('栅格导入任务已开始');
  });

  const exportRaster = (datasetId: string, name: string) => run('导出 GeoTIFF', async () => {
    const current = activeProject();
    const destination = await bridge.chooseRasterExport(name);
    if (!destination) return;
    rememberTask(await bridge.request('raster.export', { path: current.projectPath, datasetId, destination }));
    setNotice('GeoTIFF 导出任务已开始');
  });

  const importTable = (options: TableOptions) => run('开始导入表格', async () => {
    const current = activeProject();
    rememberTask(await bridge.request('table.import', { path: current.projectPath, ...options }));
    setNotice('表格导入任务已开始');
  });

  const generatePoints = (datasetId: string, xField: string, yField: string, declaredCrs: string) => run('生成点数据', async () => {
    const current = activeProject();
    rememberTask(await bridge.request('table.points', { path: current.projectPath, datasetId, xField, yField, declaredCrs }));
    setNotice('点数据生成任务已开始');
  });

  const exportTable = (datasetId: string, name: string) => run('导出表格 GeoPackage', async () => {
    const current = activeProject();
    const destination = await bridge.chooseExport(name);
    if (!destination) return;
    rememberTask(await bridge.request('table.export', { path: current.projectPath, datasetId, destination }));
    setNotice('表格导出任务已开始');
  });

  const exportVector = (datasetId: string, name: string) => run('导出 GeoPackage', async () => {
    const current = activeProject();
    const destination = await bridge.chooseExport(name);
    if (!destination) return;
    const task = await bridge.request('vector.export', { path: current.projectPath, datasetId, destination });
    rememberTask(task);
    setNotice('导出任务已开始');
  });

  const runAnalysis = (options: AnalysisOptions) => run('开始用地分析', async () => {
    const current = activeProject();
    if (activeTask) throw new Error('请等待当前任务完成。');
    const token = generation.current;
    const task = await bridge.request('analysis.run', { path: current.projectPath, ...options });
    if (generation.current !== token || projectRef.current?.id !== current.id) return;
    if (task.kind !== 'analysis') throw new Error('分析任务响应类型不匹配。');
    rememberTask(task);
    setNotice('分析任务已开始，可继续浏览地图或取消任务');
  });

  const exportAnalysisCsv = (datasetId: string, name: string) => run('导出分类统计', async () => {
    const current = activeProject();
    if (activeTask) throw new Error('请等待当前任务完成。');
    const token = generation.current;
    const destination = await bridge.chooseCsvExport(name);
    if (!destination || generation.current !== token) return;
    rememberTask(await bridge.request('analysis.exportCsv', { path: current.projectPath, datasetId, destination }));
    setNotice('统计 CSV 导出任务已开始');
  });

  useEffect(() => {
    if (!activeTask || !project || needsReopen || !runtime) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let retries = 0;
    const token = generation.current;
    const poll = async () => {
      try {
        const next = await bridge.request('task.get', { path: project.projectPath, taskId: activeTask.id });
        if (cancelled || token !== generation.current) return;
        if (next.id !== activeTask.id || next.kind !== activeTask.kind) throw new Error('任务响应与当前任务不匹配。');
        retries = 0;
        if (next.status === 'running') {
          rememberTask(next);
          timer = setTimeout(poll, 700);
        }
        else {
          const completedNotice: Record<Task['kind'], string> = { analysis: '用地分析已完成', import: '数据已导入', points: '点数据已生成', export: '数据已导出', save_as: '项目副本已创建', relocate: '来源位置已更新' };
          setNotice(next.status === 'completed' ? completedNotice[next.kind] : next.error || (next.status === 'cancelled' ? '任务已取消' : '任务未完成'));
          rememberTask(next);
          await loadWorkspace(project, token, next.status === 'completed' && next.kind !== 'export' ? next.datasetId : null);
        }
      } catch (cause) {
        if (cancelled || token !== generation.current) return;
        const kind = normalizeError(cause).data?.kind;
        if (['REQUEST_QUEUE_FULL', 'REQUEST_QUEUE_PREEMPTED', 'query_unready', 'query_timeout'].includes(kind ?? '') && retries < 3) {
          retries += 1;
          timer = setTimeout(poll, 700);
        } else {
          handleFailure(cause);
          setNotice('任务状态暂不可用，请刷新工作区重试；也可取消当前任务。');
        }
      }
    };
    timer = setTimeout(poll, 300);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [activeTask?.id, project?.id, needsReopen, runtime, bridge, rememberTask, loadWorkspace, handleFailure, pollRevision]);

  // Only a copy initiated in this live session can trigger a project transition.
  useEffect(() => {
    if (!copyIntent || busy || needsReopen || !runtime || openingCopy.current === copyIntent.taskId) return;
    if (copyIntent.token !== generation.current || projectRef.current?.id !== copyIntent.projectId) { setCopyIntent(null); return; }
    const task = workspace?.tasks.find((item) => item.id === copyIntent.taskId);
    if (!task || task.status === 'running') return;
    if (task.status !== 'completed') { setCopyIntent(null); return; }
    openingCopy.current = task.id;
    void run('打开项目副本', async () => {
      if (task.kind !== 'save_as' || !task.destination || !samePath(task.destination, copyIntent.destination)) throw new Error('另存任务响应与目标项目不匹配。');
      const next = await bridge.request('project.open', { path: task.destination });
      if (copyIntent.token !== generation.current) return;
      try {
        if (next.id === copyIntent.projectId || !samePath(next.projectPath, copyIntent.destination)) throw new Error('打开的项目与另存副本不匹配。');
        await acceptProject(next);
      } catch (cause) {
        if (copyIntent.token === generation.current) {
          generation.current += 1;
          recoveryRef.current = true;
          setNeedsReopen(true);
        }
        throw cause;
      }
      setNotice('项目已另存并打开');
    }, true).finally(() => {
      setCopyIntent((current) => current?.taskId === task.id ? null : current);
      openingCopy.current = null;
    });
  }, [copyIntent, workspace?.tasks, busy, needsReopen, runtime, bridge, run, acceptProject]);

  const cancelTask = (taskId: string) => run('取消任务', async () => {
    const current = activeProject();
    rememberTask(await bridge.request('task.cancel', { path: current.projectPath, taskId }));
    await loadWorkspace(current, generation.current);
  }, true);

  const updateLayer = (layerId: string, changes: Partial<Pick<MapLayer, 'name' | 'visible' | 'opacity' | 'color' | 'categoryField' | 'categoryColors' | 'rasterStyle'>>) => run('保存图层设置', async () => {
    const current = activeProject();
    const next = await bridge.request('layer.update', { path: current.projectPath, layerId, changes });
    setWorkspace((value) => value ? { ...value, layers: value.layers.map((layer) => layer.id === layerId ? next : layer) } : value);
    setNotice('图层设置已保存');
  });

  const reorderLayers = (layerIds: string[]) => run('调整图层顺序', async () => {
    const current = activeProject();
    const layers = await bridge.request('layer.reorder', { path: current.projectPath, layerIds });
    setWorkspace((value) => value ? { ...value, layers } : value);
  });

  const removeLayer = (layerId: string) => run('移除图层', async () => {
    const current = activeProject();
    await bridge.request('layer.remove', { path: current.projectPath, layerId });
    await loadWorkspace(current, generation.current);
  });

  return { project, draft, setDraft, setView, sessionId, runtime, workspace, selectedLayerId, selectedTableId, setSelectedLayerId, setSelectedTableId, refreshWorkspace, activeTask, copying, saveAs, relocateSource, inspectSource, inspectTable, inspectRaster, importVector, importTable, importRaster, generatePoints, exportVector, exportTable, exportRaster, runAnalysis, exportAnalysisCsv, cancelTask, updateLayer, reorderLayers, removeLayer, handleFailure, report, busy, error, notice, dirty, native, needsReopen, newProject, setNewProject, pending, requestAction, resolvePending, create, save, connect, diagnose };
}

export type WorkspaceState = ReturnType<typeof useWorkspace>;
