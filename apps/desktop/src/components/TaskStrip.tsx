import { AlertCircle, CheckCircle2, LoaderCircle, Square, XCircle } from 'lucide-react';
import type { Task } from '../../../../shared/contracts';

const stages: Record<string, string> = { starting: '正在启动', queued: '等待执行', inspecting: '检查来源', fingerprinting: '核对来源文件', reading: '读取数据', copying: '复制数据', backing_up: '备份项目数据库', relocating: '登记来源位置', converting: '转换坐标', validating: '校验数据', writing: '写入快照', exporting: '导出数据', verifying: '验证成果', verifying_copy: '校验项目副本', publishing: '登记成果', completed: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断' };
const kinds: Record<Task['kind'], string> = { analysis: '用地分析', import: '数据导入', export: '数据导出', points: '生成点数据', save_as: '项目另存为', relocate: '来源定位' };
Object.assign(stages, { reading_analysis_inputs: '读取分析输入', checking_overlaps: '检查同层重叠', intersecting: '计算面相交', validating_analysis: '校验分析成果', exporting_statistics: '导出分类统计' });

export function TaskStrip({ tasks, disabled, onCancel }: { tasks: Task[]; disabled: boolean; onCancel: (id: string) => void }) {
  if (!tasks.length) return null;
  const sorted = [...tasks].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  const active = sorted.find((task) => task.status === 'running');
  const recent = active ?? sorted[0];
  const progress = recent.completed !== null && recent.total !== null && recent.total > 0 ? { value: recent.completed, max: recent.total } : null;
  return <div className="task-strip" role="status">
    {recent.status === 'running' ? <LoaderCircle className="spin" size={15} /> : recent.status === 'completed' ? <CheckCircle2 className="success" size={15} /> : recent.status === 'failed' ? <AlertCircle className="danger" size={15} /> : <XCircle size={15} />}
    <strong>{kinds[recent.kind]}</strong><span className="task-stage">{recent.error || stages[recent.status === 'running' ? recent.stage : recent.status] || recent.stage}</span>
    {recent.status === 'running' && (progress ? <><progress value={progress.value} max={progress.max} /><span>{progress.value.toLocaleString('zh-CN')} / {progress.max.toLocaleString('zh-CN')}</span></> : <progress />)}
    {recent.destination && recent.status === 'completed' && <code title={recent.destination}>{recent.destination}</code>}
    {recent.status === 'running' && <button className="icon-button" aria-label="取消当前任务" title="取消当前任务" disabled={disabled} onClick={() => onCancel(recent.id)}><Square size={14} /></button>}
    {sorted.length > 1 && <details className="task-history"><summary>历史 {sorted.length}</summary><ul>{sorted.map((task) => <li key={task.id}><span>{kinds[task.kind]} · {stages[task.status] || task.status}</span><time>{new Date(task.createdAt).toLocaleTimeString('zh-CN')}</time>{task.error && <p>{task.error}</p>}</li>)}</ul></details>}
  </div>;
}
