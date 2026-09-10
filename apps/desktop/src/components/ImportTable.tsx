import { useEffect, useMemo, useRef, useState } from 'react';
import { FileSpreadsheet, FolderOpen, LoaderCircle, Upload } from 'lucide-react';
import type { TableInspection, TableOptions } from '../../../../shared/contracts';
import { normalizeError, type DesktopBridge } from '../bridge';
import type { WorkspaceState } from '../use-workspace';
import { displayValue } from '../vector-style';
import { Modal } from './Modal';

export function ImportTable({ bridge, state, onClose }: { bridge: DesktopBridge; state: WorkspaceState; onClose: () => void }) {
  const [sourcePath, setSourcePath] = useState('');
  const [encoding, setEncoding] = useState('utf-8-sig');
  const [delimiter, setDelimiter] = useState(',');
  const [sheet, setSheet] = useState('');
  const [headerRow, setHeaderRow] = useState('1');
  const [inspection, setInspection] = useState<TableInspection | null>(null);
  const [sheets, setSheets] = useState<string[]>([]);
  const [choosing, setChoosing] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const sequence = useRef(0);
  const xlsx = /\.xlsx$/i.test(sourcePath);
  const parsedHeader = Number(headerRow);
  const headerValid = Number.isInteger(parsedHeader) && parsedHeader > 0 && parsedHeader <= 1000;
  const locked = choosing || !!state.busy || state.needsReopen;
  const options = useMemo<TableOptions>(() => ({ sourcePath, encoding: xlsx ? null : encoding, delimiter: xlsx ? null : delimiter, sheet: xlsx ? sheet || null : null, headerRow: parsedHeader }), [sourcePath, xlsx, encoding, delimiter, sheet, parsedHeader]);

  useEffect(() => {
    const token = ++sequence.current;
    setInspection(null);
    setError('');
    setLoading(false);
    if (!sourcePath || !headerValid || state.needsReopen) return;
    const controller = new AbortController();
    setLoading(true);
    const timer = setTimeout(() => {
      void Promise.resolve().then(() => state.inspectTable(options, controller.signal)).then((result) => {
        if (sequence.current !== token) return;
        if (options.sheet && result.sheet !== options.sheet) throw new Error('预览工作表与当前选择不一致。');
        setSheets(result.sheets);
        setInspection(result);
      }).catch((cause) => {
        if (sequence.current !== token) return;
        const failure = normalizeError(cause);
        setError(failure.message);
        if (failure.data?.kind?.startsWith('ENGINE_')) state.handleFailure(cause);
      }).finally(() => { if (sequence.current === token) setLoading(false); });
    }, 180);
    return () => { sequence.current += 1; controller.abort(); clearTimeout(timer); };
  }, [options, sourcePath, headerValid, state.inspectTable, state.needsReopen, state.handleFailure]);

  const choose = async () => {
    setChoosing(true);
    setError('');
    try {
      const path = await bridge.selectTableSource();
      if (path && !Array.isArray(path)) { setSourcePath(path); setSheet(''); setSheets([]); }
    } catch (cause) { setError(normalizeError(cause).message); }
    finally { setChoosing(false); }
  };
  const canImport = !!inspection && !!inspection.columns.length && !loading && !locked && !state.activeTask && (!xlsx || !!sheet) && headerValid;
  return <Modal title="导入坐标表" className="table-import-modal" onCancel={onClose} busy={locked}>
    <div className="import-picker"><button className="button" disabled={locked} onClick={() => void choose()}><FolderOpen size={17} />选择表格文件</button><span className="muted small">CSV / XLSX</span></div>
    {sourcePath && <p className="source-location"><code>{sourcePath}</code></p>}
    {sourcePath && <div className="table-import-options">
      <div className="field"><span>格式</span><strong className="format-value">{xlsx ? 'XLSX' : 'CSV'}</strong></div>
      <label className="field">表头行<input aria-label="表头行" type="number" min={1} max={1000} step={1} value={headerRow} disabled={locked} onChange={(event) => setHeaderRow(event.target.value)} /></label>
      {xlsx ? <label className="field table-sheet-field">工作表<select aria-label="工作表" value={sheet} disabled={locked || !sheets.length} onChange={(event) => setSheet(event.target.value)}><option value="">请选择工作表</option>{sheets.map((name) => <option key={name} value={name}>{name}</option>)}</select></label> : <>
        <label className="field">字符编码<select aria-label="表格字符编码" value={encoding} disabled={locked} onChange={(event) => setEncoding(event.target.value)}><option value="utf-8-sig">UTF-8（兼容 BOM）</option><option value="GBK">GBK</option><option value="GB18030">GB18030</option></select></label>
        <label className="field">分隔符<select aria-label="分隔符" value={delimiter} disabled={locked} onChange={(event) => setDelimiter(event.target.value)}><option value=",">逗号</option><option value={'\t'}>制表符</option><option value=";">分号</option><option value="|">竖线</option></select></label>
      </>}
    </div>}
    {!headerValid && <p className="inline-error" role="alert">表头行必须为 1 至 1000 的整数。</p>}
    {sourcePath && <p className="import-data-policy">{xlsx ? '公式保留为文本，不执行计算；原工作簿版式不导入。' : '单元格按文本保留，空字符串与文字 NULL 不自动转换。'}</p>}
    {loading && <div className="query-empty" role="status"><LoaderCircle size={19} className="spin" /><span>正在读取表格预览</span></div>}
    {!loading && inspection && (!xlsx || !!sheet) && <section className="table-preview" aria-label="表格预览"><div className="preview-heading"><strong>预览 {inspection.rows.length} 行</strong><span>{inspection.truncated ? '部分记录' : '预览不代表完整校验'}</span></div><div className="table-preview-scroll"><table><thead><tr><th>源行号</th>{inspection.columns.map((column) => <th key={column.index} title={column.sourceName ?? '空表头'}>{column.fieldName}</th>)}</tr></thead><tbody>{inspection.rows.map((row) => <tr key={row.sourceRow}><th>{row.sourceRow}</th>{inspection.columns.map((column) => <td key={column.index} className={row.values[column.fieldName] === null ? 'null-value' : undefined}>{displayValue(row.values[column.fieldName])}</td>)}</tr>)}</tbody></table></div>{!inspection.rows.length && <p className="query-empty">没有可预览的数据行</p>}</section>}
    {!sourcePath && <div className="query-empty"><FileSpreadsheet size={23} /><span>未选择表格</span></div>}
    {!!inspection?.warnings.length && <ul className="data-warnings">{inspection.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
    {(error || state.error) && <div className="inline-error" role="alert">{error || state.error?.message}</div>}
    <div className="modal-actions"><button className="button" disabled={locked} onClick={onClose}>取消</button><button className="button primary" disabled={!canImport} onClick={async () => { if (canImport && await state.importTable(options)) onClose(); }}><Upload size={16} />导入表格</button></div>
  </Modal>;
}
