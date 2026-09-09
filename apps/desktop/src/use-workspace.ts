import { useCallback, useEffect, useRef, useState } from 'react';
import type { EngineError, ProbeReport, Project, RuntimeInfo } from '../../../shared/contracts';
import { normalizeError, type DesktopBridge } from './bridge';
import { draftFromProject, hasUnsavedChanges, validateDirectoryName, type ProjectDraft } from './project-state';

export type ProjectAction = 'new' | 'open' | 'close' | 'exit';

export function useWorkspace(bridge: DesktopBridge) {
  const [project, setProject] = useState<Project | null>(null);
  const [draft, setDraft] = useState<ProjectDraft | null>(null);
  const [runtime, setRuntime] = useState<RuntimeInfo | null>(null);
  const [report, setReport] = useState<ProbeReport | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<EngineError | null>(null);
  const [notice, setNotice] = useState('');
  const [newProject, setNewProject] = useState(false);
  const [pending, setPending] = useState<ProjectAction | null>(null);
  const [needsReopen, setNeedsReopen] = useState(false);
  const [native] = useState(() => bridge.available());
  const inFlight = useRef(false);
  const projectRef = useRef<Project | null>(null);
  const recoveryRef = useRef(false);
  projectRef.current = project;
  const dirty = hasUnsavedChanges(project, draft);

  const run = useCallback(async (label: string, action: () => Promise<void>): Promise<boolean> => {
    if (inFlight.current) return false;
    inFlight.current = true;
    setBusy(label);
    setError(null);
    setNotice('');
    try {
      await action();
      return true;
    } catch (cause) {
      const failure = normalizeError(cause);
      setError(failure);
      if (failure.data?.kind?.startsWith('ENGINE_')) {
        setRuntime(null);
        if (projectRef.current) {
          recoveryRef.current = true;
          setNeedsReopen(true);
        }
      }
      return false;
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  }, []);

  const acceptProject = (next: Project) => {
    setProject(next);
    setDraft(draftFromProject(next));
    setReport(null);
    recoveryRef.current = false;
    setNeedsReopen(false);
  };

  const connect = useCallback(() => run('连接引擎', async () => {
    setRuntime(null);
    const info = await bridge.request('runtime.info');
    if (info.protocolVersion !== 1) throw new Error('引擎协议版本不兼容，请检查桌面应用和引擎版本。');
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
      displayCrs: project.displayCrs,
      viewState: project.viewState,
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
        acceptProject(await bridge.request('project.open', { path }));
        setNotice('项目已打开');
      });
      return;
    }
    if (action === 'close') {
      await run('关闭项目', async () => {
        await bridge.request('project.close');
        setProject(null);
        setDraft(null);
        setReport(null);
        recoveryRef.current = false;
        setNeedsReopen(false);
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
    if (inFlight.current) return;
    if (dirty) { setPending(action); return; }
    void executeAction(action);
  };

  const closeState = useRef({ busy, dirty, requestAction });
  closeState.current = { busy, dirty, requestAction };
  useEffect(() => {
    if (!native) return;
    let disposed = false;
    let unlisten: (() => void) | undefined;
    void bridge.onClose((preventDefault) => {
      if (closeState.current.busy || closeState.current.dirty) {
        preventDefault();
        if (!closeState.current.busy) closeState.current.requestAction('exit');
      }
    }).then((cleanup) => { if (disposed) cleanup(); else unlisten = cleanup; }).catch((cause) => setError(normalizeError(cause)));
    return () => { disposed = true; unlisten?.(); };
  }, [native, bridge]);

  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {
      if (dirty || busy) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [dirty, busy]);

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
    acceptProject(await bridge.request('project.create', { directory, name }));
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

  return { project, draft, setDraft, runtime, report, busy, error, notice, dirty, native, needsReopen, newProject, setNewProject, pending, requestAction, resolvePending, create, save, connect, diagnose };
}
