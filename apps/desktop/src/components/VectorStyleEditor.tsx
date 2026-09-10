import { useEffect, useState } from 'react';
import { Check, Plus, Trash2, Undo2 } from 'lucide-react';
import type { FieldValue, LayerChanges, MapLayer, VectorCartographySpec, VectorDataset } from '../../../../shared/contracts';
import { CATEGORY_LIMIT, cartographyValidationError, makeCartographySpec, presetPalettes, typedCategoryKey, typedCategoryLabel } from '../vector-style';

type CategoryRenderer = Extract<VectorCartographySpec['renderer'], { kind: 'categorized' }>;
type DraftState = { draft: VectorCartographySpec | null; baseline: string; revision: number };
const snapshot = (layer: MapLayer): DraftState => ({ draft: layer.cartography ? structuredClone(layer.cartography) : null, baseline: JSON.stringify(layer.cartography ?? null), revision: layer.cartographyRevision ?? 0 });

export function VectorStyleEditor({ dataset, layer, disabled, onSave }: {
  dataset: VectorDataset;
  layer: MapLayer;
  disabled: boolean;
  onSave: (changes: LayerChanges) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState<DraftState>(() => snapshot(layer));
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState('');
  const [valueType, setValueType] = useState<'string' | 'number' | 'boolean'>('string');
  const [value, setValue] = useState('');
  const [label, setLabel] = useState('');
  const [color, setColor] = useState('#7eac84');
  const [addError, setAddError] = useState('');
  const { draft, revision } = editing;
  const dirty = JSON.stringify(draft) !== editing.baseline;
  const incomingRevision = layer.cartographyRevision ?? 0;
  const conflict = incomingRevision > revision;
  const locked = disabled || saving;
  const reset = () => { setEditing(snapshot(layer)); setFailure(''); setAddError(''); };

  useEffect(() => { reset(); setValue(''); setLabel(''); }, [layer.id, dataset.id, dataset.version]);
  useEffect(() => {
    if (!dirty && !saving && incomingRevision > revision) setEditing(snapshot(layer));
  }, [incomingRevision, dirty, saving, revision]);

  const change = (update: (current: VectorCartographySpec) => VectorCartographySpec) => {
    setEditing((current) => current.draft ? { ...current, draft: update(current.draft) } : current);
    setFailure('');
  };
  const changeRenderer = (update: (current: CategoryRenderer) => CategoryRenderer) => change((current) => current.renderer.kind === 'categorized' ? { ...current, renderer: update(current.renderer) } : current);
  const usePreset = (preset: VectorCartographySpec['basePreset']) => {
    const next = makeCartographySpec(dataset, preset, layer.name, revision + 1);
    if (draft) {
      next.legend = { ...draft.legend };
      if (draft.renderer.kind === 'categorized') next.renderer = { ...draft.renderer, categories: draft.renderer.categories.map((entry, index) => ({ ...entry, color: presetPalettes[preset][index % presetPalettes[preset].length] })) };
    }
    setEditing((current) => ({ ...current, draft: next }));
    setFailure('');
    setColor(presetPalettes[preset][0]);
  };
  const chooseField = (field: string) => change((current) => ({ ...current, renderer: field
    ? { kind: 'categorized', field, categories: [], nullColor: '#a0a6ac', nullLabel: 'NULL', otherColor: '#dadde1', otherLabel: '其他' }
    : { kind: 'single' } }));
  const addCategory = () => {
    if (!draft || draft.renderer.kind !== 'categorized') return;
    let parsed: Exclude<FieldValue, null> = value;
    if (valueType === 'number') {
      parsed = Number(value);
      if (!value.trim() || !Number.isFinite(parsed) || Math.abs(parsed) > Number.MAX_SAFE_INTEGER) { setAddError('请输入有限安全数值；较大整数请使用文字。'); return; }
    } else if (valueType === 'boolean') parsed = value === 'true';
    if (draft.renderer.categories.some((entry) => typedCategoryKey(entry.value) === typedCategoryKey(parsed))) { setAddError('同一类型和值的类别已存在。'); return; }
    const entry = { value: parsed, label: label || Array.from(typedCategoryLabel(parsed)).slice(0, 120).join(''), color };
    const candidate: VectorCartographySpec = { ...draft, renderer: { ...draft.renderer, categories: [...draft.renderer.categories, entry] } };
    const invalid = cartographyValidationError(candidate, dataset);
    if (invalid) { setAddError(invalid); return; }
    change(() => candidate);
    setValue(valueType === 'boolean' ? 'false' : '');
    setLabel('');
    setAddError('');
  };
  const save = async (restore: boolean) => {
    if (locked || conflict || (!restore && !draft)) return;
    const cartography = restore ? null : { ...structuredClone(draft!), revision: revision + 1 };
    setSaving(true);
    setFailure('');
    try {
      if (await onSave({ cartography, expectedCartographyRevision: revision })) {
        setEditing({ draft: cartography, baseline: JSON.stringify(cartography), revision: revision + 1 });
      } else setFailure('保存未完成，草稿已保留。');
    } catch {
      setFailure('保存未完成，草稿已保留。');
    } finally { setSaving(false); }
  };
  const invalid = draft ? cartographyValidationError(draft, dataset) : null;
  const renderer = draft?.renderer.kind === 'categorized' ? draft.renderer : null;

  return <section className="vector-style-editor" aria-label="专题样式设置">
    <div className="vector-style-heading"><h3>专题样式</h3><span>{layer.cartography ? '已采用' : '可选'}</span></div>
    <div className="vector-preset-buttons"><button type="button" className="button compact" disabled={locked} onClick={() => usePreset('planning')}>采用 Planning 预设</button><button type="button" className="button compact" disabled={locked} onClick={() => usePreset('publication')}>采用 Publication 预设</button></div>
    <p className="muted small">Planning 为通用规划配色；Publication 为简洁论文配色。可手动调整，不代表地类标准或期刊合规。</p>
    {!draft && <p className="muted small">选取预设后编辑草稿，应用时保存。</p>}
    {draft && <fieldset disabled={locked} className="vector-style-fields">
      <p className="vector-style-origin">起始预设：{draft.basePreset === 'planning' ? 'Planning' : 'Publication'} · v{draft.presetVersion}</p>
      <label className="field">专题渲染<select aria-label="专题渲染" value={renderer?.field ?? ''} onChange={(event) => chooseField(event.target.value)}><option value="">单一符号</option>{dataset.fields.map((field) => <option key={field.name} value={field.name}>{field.alias || field.name}</option>)}</select></label>
      <div className="vector-symbol-grid">
        {!renderer && <label className="color-field">填色<input type="color" aria-label="专题填色" value={draft.symbol.fillColor} onChange={(event) => change((current) => ({ ...current, symbol: { ...current.symbol, fillColor: event.target.value } }))} /></label>}
        <label className="color-field">描边<input type="color" aria-label="专题描边" value={draft.symbol.strokeColor} onChange={(event) => change((current) => ({ ...current, symbol: { ...current.symbol, strokeColor: event.target.value } }))} /></label>
        <label className="field">线宽 · pt<input type="number" aria-label="专题线宽" min={0} max={6} step={0.05} value={Number.isFinite(draft.symbol.strokeWidthPt) ? draft.symbol.strokeWidthPt : ''} onChange={(event) => change((current) => ({ ...current, symbol: { ...current.symbol, strokeWidthPt: event.target.valueAsNumber } }))} /></label>
        <label className="field">点半径 · pt<input type="number" aria-label="专题点半径" min={1} max={16} step={0.25} value={Number.isFinite(draft.symbol.pointRadiusPt) ? draft.symbol.pointRadiusPt : ''} onChange={(event) => change((current) => ({ ...current, symbol: { ...current.symbol, pointRadiusPt: event.target.valueAsNumber } }))} /></label>
      </div>
      {renderer && <div className="vector-categories">
        <p className="muted small">手工配置 {renderer.categories.length} / {CATEGORY_LIMIT} 个类别。文字空值可直接添加；分类线使用类别色，面和点保留统一描边。</p>
        <div className="vector-category-list">{renderer.categories.map((entry, index) => <div className="vector-category-item" key={typedCategoryKey(entry.value)}>
          <code title={typedCategoryLabel(entry.value)}>{typedCategoryLabel(entry.value)}</code>
          <div><input aria-label={`类别 ${index + 1} 标签`} value={entry.label} onChange={(event) => changeRenderer((current) => ({ ...current, categories: current.categories.map((item, i) => i === index ? { ...item, label: event.target.value } : item) }))} /><input type="color" aria-label={`类别 ${index + 1} 颜色`} value={entry.color} onChange={(event) => changeRenderer((current) => ({ ...current, categories: current.categories.map((item, i) => i === index ? { ...item, color: event.target.value } : item) }))} /><button type="button" className="icon-button" aria-label={`删除类别 ${index + 1}`} onClick={() => changeRenderer((current) => ({ ...current, categories: current.categories.filter((_, i) => i !== index) }))}><Trash2 size={14} /></button></div>
        </div>)}</div>
        <div className="vector-category-add">
          <label className="field">类别类型<select aria-label="新类别类型" value={valueType} onChange={(event) => { const type = event.target.value as typeof valueType; setValueType(type); setValue(type === 'boolean' ? 'false' : ''); setAddError(''); }}><option value="string">文字</option><option value="number">数值</option><option value="boolean">布尔</option></select></label>
          <label className="field">类别值{valueType === 'boolean' ? <select aria-label="新类别值" value={value} onChange={(event) => setValue(event.target.value)}><option value="false">false</option><option value="true">true</option></select> : <input aria-label="新类别值" value={value} placeholder={valueType === 'string' ? '留空表示空字符串' : '有限安全数值'} onChange={(event) => setValue(event.target.value)} />}</label>
          <label className="field">图例标签<input aria-label="新类别标签" value={label} placeholder="可留空，使用类型和值" onChange={(event) => setLabel(event.target.value)} /></label>
          <div className="vector-category-add-actions"><input type="color" aria-label="新类别颜色" value={color} onChange={(event) => setColor(event.target.value)} /><button type="button" className="button compact" disabled={renderer.categories.length >= CATEGORY_LIMIT} onClick={addCategory}><Plus size={13} />添加类别</button></div>
          {addError && <p className="inline-error" role="alert">{addError}</p>}
        </div>
        {(['null', 'other'] as const).map((kind) => <div className="vector-special-category" key={kind}><label className="field">{kind === 'null' ? 'NULL 标签' : '其他值标签'}<input aria-label={kind === 'null' ? 'NULL 标签' : '其他值标签'} value={renderer[`${kind}Label`]} onChange={(event) => changeRenderer((current) => ({ ...current, [`${kind}Label`]: event.target.value }))} /></label><input type="color" aria-label={kind === 'null' ? 'NULL 颜色' : '其他值颜色'} value={renderer[`${kind}Color`]} onChange={(event) => changeRenderer((current) => ({ ...current, [`${kind}Color`]: event.target.value }))} /></div>)}
      </div>}
      <label className="field">图例标题<input aria-label="图例标题" value={draft.legend.title} onChange={(event) => change((current) => ({ ...current, legend: { ...current.legend, title: event.target.value } }))} /></label>
      <label className="vector-legend-toggle"><input type="checkbox" checked={draft.legend.visible} onChange={(event) => change((current) => ({ ...current, legend: { ...current.legend, visible: event.target.checked } }))} />显示图例</label>
      <p className="muted small">图例说明已配置符号，不表示类别存在、数量或完整覆盖。</p>
      {invalid && <p className="inline-error" role="alert">{invalid}</p>}
      <div className="vector-style-actions"><button type="button" className="button compact" aria-label="重置专题草稿" disabled={!dirty && !conflict} onClick={reset}><Undo2 size={13} />重置草稿</button><button type="button" className="button compact primary" disabled={!dirty || !!invalid || conflict} onClick={() => void save(false)}><Check size={13} />应用专题样式</button></div>
    </fieldset>}
    {layer.cartography && <button type="button" className="vector-restore-link" disabled={locked || conflict} onClick={() => void save(true)}>恢复原有样式</button>}
    {conflict && <p className="inline-error" role="alert">已保存样式发生变化，请重置草稿后再应用。</p>}
    {failure && <p className="inline-error" role="alert">{failure}</p>}
  </section>;
}
