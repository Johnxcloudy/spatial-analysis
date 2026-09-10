import { useEffect, useState } from 'react';
import type { AnalysisOptions, VectorDataset } from '../../../../shared/contracts';
import type { DesktopBridge } from '../bridge';
import type { WorkspaceState } from '../use-workspace';
import { useQueuedQuery } from '../use-queued-query';

const textFields = (dataset?: VectorDataset) => dataset?.fields.filter((field) => /^(string|text)/i.test(field.storageType)) ?? [];
const number = (value: number) => value.toLocaleString('zh-CN', { maximumFractionDigits: 6 });
const category = (value: string | null) => value === null ? '未分类（NULL）' : JSON.stringify(value);
const ratio = (value: number | null) => value === null ? '不适用' : `${number(value * 100)}%`;

export function AnalysisWorkspace({ bridge, state, visible, onShowMap }: { bridge: DesktopBridge; state: WorkspaceState; visible: boolean; onShowMap: () => void }) {
  const datasets = state.workspace?.datasets ?? [];
  const polygons = datasets.filter((dataset): dataset is VectorDataset => dataset.kind === 'vector' && ['Polygon', 'MultiPolygon'].includes(dataset.geometryType) && !!dataset.crsWkt);
  const results = datasets.filter((dataset): dataset is VectorDataset => dataset.kind === 'vector' && dataset.source.driver === 'SpatialAnalysis');
  const [options, setOptions] = useState<AnalysisOptions>({ operation: 'clip', name: '', inputDatasetId: '', overlayDatasetId: '', inputClassField: '', overlayClassField: null, classificationStandard: '', analysisCrs: state.project?.analysisCrs ?? '', crsReason: '' });
  const [resultId, setResultId] = useState('');
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const latestAnalysis = state.workspace?.tasks.find((task) => task.kind === 'analysis' && task.status === 'completed' && task.datasetId);
  useEffect(() => { if (latestAnalysis?.datasetId) { setResultId(latestAnalysis.datasetId); setOffset(0); } }, [latestAnalysis?.id, latestAnalysis?.datasetId]);
  const input = polygons.find((dataset) => dataset.id === options.inputDatasetId);
  const overlay = polygons.find((dataset) => dataset.id === options.overlayDatasetId);
  const result = results.find((dataset) => dataset.id === resultId) ?? results[results.length - 1];
  const enabled = state.native && !!state.runtime && !!state.project && !state.needsReopen;
  const blocked = !enabled || !!state.busy || !!state.activeTask || state.copying;
  const valid = !!input && !!overlay && !!options.name.trim() && !!options.classificationStandard.trim() && !!options.analysisCrs.trim() && !!options.crsReason.trim() && textFields(input).some((field) => field.name === options.inputClassField) && (options.operation === 'clip' || textFields(overlay).some((field) => field.name === options.overlayClassField));
  const change = (values: Partial<AnalysisOptions>) => setOptions((current) => ({ ...current, ...values }));
  useEffect(() => { setOffset(0); }, [result?.id]);
  const { value: page, loading } = useQueuedQuery(JSON.stringify([state.project?.projectPath, result?.id, result?.version, offset, revision]), enabled && visible && !!result, async (signal) => {
    const next = await bridge.request('analysis.result', { path: state.project!.projectPath, datasetId: result!.id, offset, limit: 100 }, signal);
    if (next.datasetId !== result!.id || next.version !== result!.version || next.record.resultDatasetId !== result!.id || next.offset !== offset) throw new Error('统计响应与当前成果不匹配。');
    return next;
  }, state.handleFailure);
  const showResult = () => {
    const layer = state.workspace?.layers.find((item) => item.datasetId === result?.id);
    if (layer) state.setSelectedLayerId(layer.id);
    onShowMap();
  };
  return <div className="analysis-workspace" aria-label="用地分析工作区">
    <section className="analysis-setup">
      <h1>用地叠加与分类面积</h1>
      <p className="muted">使用完整数据快照，不使用当前视窗或属性筛选。未运行的配置仅保留在本次会话；成功结果保留完整分析记录。</p>
      <form aria-label="分析配置" onSubmit={(event) => { event.preventDefault(); if (valid && !blocked) void state.runAnalysis(options); }}>
        <fieldset disabled={blocked} className="analysis-fields">
          <label className="field">分析方式<select aria-label="分析方式" value={options.operation} onChange={(event) => change({ operation: event.target.value as AnalysisOptions['operation'], overlayClassField: null })}><option value="clip">研究区裁剪</option><option value="intersect">两层相交</option></select></label>
          <label className="field">成果名称<input aria-label="成果名称" required maxLength={120} value={options.name} onChange={(event) => change({ name: event.target.value })} /></label>
          <label className="field">用地数据集<select aria-label="用地数据集" value={options.inputDatasetId} onChange={(event) => change({ inputDatasetId: event.target.value, inputClassField: '' })}><option value="">选择面数据</option>{polygons.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <label className="field">叠加数据集<select aria-label="叠加数据集" value={options.overlayDatasetId} onChange={(event) => change({ overlayDatasetId: event.target.value, overlayClassField: null })}><option value="">选择研究区或右侧面层</option>{polygons.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <label className="field">左侧地类字段<select aria-label="左侧地类字段" value={options.inputClassField} onChange={(event) => change({ inputClassField: event.target.value })}><option value="">选择字符串字段</option>{textFields(input).map((field) => <option key={field.name} value={field.name}>{field.name}</option>)}</select></label>
          {options.operation === 'intersect' && <label className="field">右侧地类字段<select aria-label="右侧地类字段" value={options.overlayClassField ?? ''} onChange={(event) => change({ overlayClassField: event.target.value || null })}><option value="">选择字符串字段</option>{textFields(overlay).map((field) => <option key={field.name} value={field.name}>{field.name}</option>)}</select></label>}
          <label className="field">分类标准及版本<input aria-label="分类标准及版本" required maxLength={200} value={options.classificationStandard} onChange={(event) => change({ classificationStandard: event.target.value })} placeholder="明确填写采用的分类体系" /></label>
          <label className="field">分析投影<input aria-label="分析投影" required value={options.analysisCrs} onChange={(event) => change({ analysisCrs: event.target.value })} placeholder="适用的二维投影 EPSG 或 WKT" /></label>
          <label className="field analysis-wide">投影适用理由<textarea aria-label="投影适用理由" required maxLength={200} rows={2} value={options.crsReason} onChange={(event) => change({ crsReason: event.target.value })} placeholder="研究区位置、分带和投影适用范围" /></label>
        </fieldset>
        <div className="analysis-policy"><strong>严格校验与面积口径</strong><p>不自动修复、吸附、去重或删除碎面。空/无效几何及同层正面积重叠阻止分析；裁剪研究区先取并集。面积为适用二维投影下的几何面积，拒绝经纬度和 Web Mercator。</p><p>NULL、空字符串和文字 “NULL” 分组不同，保留前导零及空白。台账面积不替代几何面积。</p></div>
        {[input, overlay].map((item, index) => item && <p className="analysis-input-version" key={index}>{index === 0 ? '左侧' : '右侧'}：{item.name} · {item.featureCount?.toLocaleString()} 要素 · <code>{item.version}</code></p>)}
        <button className="button primary" disabled={blocked || !valid}>开始分析</button>
      </form>
    </section>
    <section className="analysis-results" aria-label="分类面积成果">
      <div className="section-heading"><h2>已登记成果</h2><select aria-label="分析成果" value={result?.id ?? ''} onChange={(event) => { setResultId(event.target.value); setOffset(0); }}><option value="" disabled>暂无成果</option>{results.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div>
      {result && <div className="analysis-actions"><button className="button" disabled={!enabled} onClick={showResult}>查看成果图斑</button><button className="button" disabled={!enabled || loading} onClick={() => setRevision((value) => value + 1)}>刷新统计</button><button className="button" disabled={blocked} onClick={() => void state.exportVector(result.id, result.name)}>导出成果 GeoPackage</button><button className="button" disabled={blocked} onClick={() => void state.exportAnalysisCsv(result.id, result.name)}>导出统计 CSV</button></div>}
      {loading && <p role="status">正在读取分类统计</p>}
      {page && <>
        <dl className="analysis-metrics"><div><dt>研究区面积 m²</dt><dd>{number(page.record.studyAreaM2)}</dd></div><div><dt>有效覆盖面积 m²</dt><dd>{number(page.record.coveredAreaM2)}</dd></div><div><dt>未覆盖面积 m²</dt><dd>{number(page.record.uncoveredAreaM2)}</dd></div><div><dt>成果图斑</dt><dd>{page.record.outputFeatureCount.toLocaleString()}</dd></div></dl>
        <p>{page.record.areaMethod === 'projected_planar' && page.record.overlapPolicy === 'reject_positive_area' && page.record.geometryPolicy === 'no_repair_no_snap_no_sliver_removal'
          ? '采用投影平面面积；分类输入已通过同层正面积重叠校验。在此口径下，记录面积之和等于不重复的有效覆盖面积。叠加层并集定义研究区，未覆盖面积为研究区面积减去有效覆盖面积；浮点残差不强制归零。'
          : page.record.coverageExplanation}</p>
        <div className="analysis-table-scroll"><table aria-label="分类面积统计"><thead><tr><th>左侧地类</th>{page.record.operation === 'intersect' && <th>右侧地类</th>}<th>图斑数</th><th>面积 m²</th><th>公顷</th><th>亩</th><th>占研究区</th><th>占有效覆盖</th></tr></thead><tbody>{page.rows.map((row, index) => <tr key={page.offset + index}><td>{category(row.inputClass)}</td>{page.record.operation === 'intersect' && <td>{category(row.overlayClass)}</td>}<td>{row.featureCount}</td><td>{number(row.areaM2)}</td><td>{number(row.areaHa)}</td><td>{number(row.areaMu)}</td><td>{ratio(row.studyRatio)}</td><td>{ratio(row.coverageRatio)}</td></tr>)}</tbody></table></div>
        <div className="pagination"><span>{page.total ? page.offset + 1 : 0}–{page.offset + page.rows.length} / {page.total} 类</span><button className="button" aria-label="统计上一页" disabled={loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>上一页</button><button className="button" aria-label="统计下一页" disabled={loading || !page.hasMore} onClick={() => setOffset(offset + page.rows.length)}>下一页</button></div>
        <details className="analysis-record"><summary>分析追溯与口径</summary><dl><dt>分类标准</dt><dd>{page.record.classificationStandard}</dd><dt>分析投影</dt><dd>{page.record.analysisCrsAuthority ?? page.record.analysisCrsWkt}</dd><dt>适用理由</dt><dd>{page.record.crsReason}</dd><dt>记录面积之和 m²</dt><dd>{number(page.record.recordAreaM2)}</dd><dt>仅边/点接触数</dt><dd>{page.record.boundaryContactCount}</dd></dl><pre>{JSON.stringify(page.record, null, 2)}</pre></details>
      </>}
      {!result && <p className="muted">完成分析后，成果和统计将在这里显示。重开项目可继续查询和导出。</p>}
    </section>
  </div>;
}
