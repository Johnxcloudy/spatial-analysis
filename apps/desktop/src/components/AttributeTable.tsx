import { useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Filter, LoaderCircle, Search, X } from 'lucide-react';
import type { AttributePage, AttributeFilter, TableDataset, VectorDataset, FeatureResult } from '../../../../shared/contracts';
import type { AttributeQuery } from '../use-vector-data';
import { displayValue } from '../vector-style';

export function AttributeTable({ dataset, page, loading, query, setQuery, selectedId, selected, selectedSourceRow, onSelect, onClear, enabled }: {
  dataset: VectorDataset | TableDataset | undefined;
  page: AttributePage | null;
  loading: boolean;
  query: AttributeQuery;
  setQuery: (query: AttributeQuery) => void;
  selectedId: string | null;
  selected: FeatureResult | null;
  selectedSourceRow?: string | null;
  onSelect: (id: string) => void;
  onClear: () => void;
  enabled: boolean;
}) {
  const [field, setField] = useState('');
  const [operator, setOperator] = useState<AttributeFilter['operator']>('contains');
  const [value, setValue] = useState('');
  const previousOffsets = useRef<number[]>([]);
  const filterKey = JSON.stringify(query.filter);
  useEffect(() => { setField(dataset?.fields[0]?.name ?? ''); setValue(''); setOperator('contains'); }, [dataset?.id]);
  useEffect(() => { previousOffsets.current = []; }, [dataset?.id, dataset?.version, query.limit, query.sortField, query.descending, filterKey]);
  const fields = page?.fields ?? dataset?.fields ?? [];
  const hasSourceRow = dataset?.kind === 'table' || dataset?.source.driver === 'TablePoints';
  const sourceRow = selectedSourceRow ?? selected?.row.sourceRow ?? page?.rows.find((row) => row.id === selectedId)?.sourceRow;
  const selectedOutsidePage = selectedId !== null && !page?.rows.some((row) => row.id === selectedId);
  const changeSort = (name: string) => setQuery({ ...query, offset: 0, sortField: name, descending: query.sortField === name ? !query.descending : false });
  return <section className="attribute-section" aria-label="属性表">
    <div className="attribute-heading"><h2>{dataset?.name ?? '属性表'}</h2><span>{page ? `${page.total.toLocaleString('zh-CN')} 条` : dataset ? `${dataset.featureCount.toLocaleString('zh-CN')} ${dataset.kind === 'table' ? '条记录' : '个要素'}` : '未选择数据'}</span>{loading && <LoaderCircle size={15} className="spin" />}</div>
    <form className="attribute-filter" onSubmit={(event) => { event.preventDefault(); if (field) setQuery({ ...query, offset: 0, filter: { field, operator, value } }); }}>
      <Filter size={14} /><select aria-label="筛选字段" value={field} disabled={!dataset || !enabled || loading} onChange={(event) => setField(event.target.value)}>{dataset?.fields.map((item) => <option key={item.name} value={item.name}>{item.alias || item.name}</option>)}</select><select aria-label="筛选条件" value={operator} disabled={!dataset || !enabled || loading} onChange={(event) => setOperator(event.target.value as AttributeFilter['operator'])}><option value="contains">包含</option><option value="equals">等于</option><option value="isNull">为空</option></select><input aria-label="筛选值" value={value} disabled={!dataset || !enabled || operator === 'isNull' || loading} onChange={(event) => setValue(event.target.value)} /><button className="icon-button" title="应用筛选" aria-label="应用筛选" disabled={!field || !enabled || loading}><Search size={16} /></button><button type="button" className="icon-button" title="清除筛选" aria-label="清除筛选" disabled={!query.filter || !enabled || loading} onClick={() => { setValue(''); setQuery({ ...query, offset: 0, filter: null }); }}><X size={16} /></button>
    </form>
    {selectedId !== null && <div className="selected-record"><strong>选中 ID：{selectedId}</strong>{hasSourceRow && <span>源行号：{sourceRow ?? '未知'}</span>}{selectedOutsidePage && <span>不在当前页</span>}<button className="icon-button" aria-label={dataset?.kind === 'table' ? '清除记录选择' : '清除要素选择'} title={dataset?.kind === 'table' ? '清除记录选择' : '清除要素选择'} onClick={onClear}><X size={14} /></button>{selectedOutsidePage && selected && <dl>{Object.entries(selected.row.values).map(([name, cell]) => <div key={name}><dt>{name}</dt><dd>{displayValue(cell)}</dd></div>)}</dl>}</div>}
    <div className="attribute-scroll" aria-busy={loading}>
      {page && <table><thead><tr><th className="row-id">ID</th>{hasSourceRow && <th>源行号</th>}{fields.map((item) => <th key={item.name}><button disabled={loading || !enabled} onClick={() => changeSort(item.name)} title={`${item.name} 排序`}>{item.alias || item.name}{query.sortField === item.name && (query.descending ? <ArrowDown size={12} /> : <ArrowUp size={12} />)}</button></th>)}</tr></thead><tbody>{page.rows.map((row) => <tr key={row.id} className={selectedId === row.id ? 'selected' : ''} aria-selected={selectedId === row.id} onClick={() => onSelect(row.id)}><th><button title={`选择${dataset?.kind === 'table' ? '记录' : '要素'} ${row.id}`} onClick={(event) => { event.stopPropagation(); onSelect(row.id); }}>{row.id}</button></th>{hasSourceRow && <td>{row.sourceRow ?? '未知'}</td>}{fields.map((item) => <td key={item.name} title={displayValue(row.values[item.name])} className={row.values[item.name] === null ? 'null-value' : undefined}>{displayValue(row.values[item.name])}</td>)}</tr>)}</tbody></table>}
      {!page && <div className="query-empty">{loading ? <><LoaderCircle size={19} className="spin" /><span>正在读取属性</span></> : <span>{dataset ? enabled ? '暂无属性记录' : '工作区暂不可用' : '未选择图层'}</span>}</div>}
      {page && !page.rows.length && <div className="query-empty">没有符合条件的记录</div>}
    </div>
    <div className="pagination"><span>{page?.truncated ? '本页达到响应大小限制' : page ? `${page.total ? page.offset + 1 : 0}–${page.offset + page.rows.length} / ${page.total}` : '0 / 0'}</span><label>每页<select aria-label="每页记录数" value={query.limit} disabled={!dataset || !enabled || loading} onChange={(event) => setQuery({ ...query, offset: 0, limit: Number(event.target.value) })}><option value={50}>50</option><option value={100}>100</option><option value={200}>200</option><option value={500}>500</option></select></label><button className="icon-button" aria-label="上一页" title="上一页" disabled={!enabled || loading || !page || page.offset === 0} onClick={() => setQuery({ ...query, offset: previousOffsets.current.pop() ?? Math.max(0, query.offset - query.limit) })}><ChevronLeft size={17} /></button><button className="icon-button" aria-label="下一页" title="下一页" disabled={!enabled || loading || !page?.hasMore || !page.rows.length} onClick={() => { previousOffsets.current.push(query.offset); setQuery({ ...query, offset: query.offset + (page?.rows.length ?? query.limit) }); }}><ChevronRight size={17} /></button><button className="button compact" aria-label="属性末页" disabled={!enabled || loading || !page || page.total === 0 || query.offset >= Math.floor((page.total - 1) / query.limit) * query.limit} onClick={() => { if (page) { previousOffsets.current.push(query.offset); setQuery({ ...query, offset: Math.floor((page.total - 1) / query.limit) * query.limit }); } }}>末页</button></div>
  </section>;
}
