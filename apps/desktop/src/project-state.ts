import type { Project } from '../../../shared/contracts';

export interface ProjectDraft {
  name: string;
  description: string;
  analysisCrs: string;
}

export const draftFromProject = (project: Project): ProjectDraft => ({
  name: project.name,
  description: project.description,
  analysisCrs: project.analysisCrs ?? '',
});

export const hasUnsavedChanges = (project: Project | null, draft: ProjectDraft | null): boolean => !!project && !!draft && (
  project.name !== draft.name || project.description !== draft.description || (project.analysisCrs ?? '') !== draft.analysisCrs
);

export function validateDirectoryName(name: string): string | null {
  if (!name.trim()) return '请输入项目名称。';
  if (name !== name.trim() || name.endsWith('.')) return '项目名称不能以空格或句点结尾，也不能以空格开头。';
  if (name === '.' || name === '..' || /[<>:"/\\|?*\u0000-\u001f]/.test(name)) return '项目名称含有 Windows 文件夹不支持的字符。';
  if (/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(name)) return '该名称是 Windows 保留名称，请使用其他名称。';
  if (name.length > 100) return '项目名称不能超过 100 个字符。';
  return null;
}
