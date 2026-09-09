import { useEffect, useState } from 'react';
import { Check, RefreshCw, Undo2 } from 'lucide-react';
import type { RasterDataset, RasterStyle } from '../../../../shared/contracts';

export function RasterStyleEditor({ dataset, style, disabled, onSave }: { dataset: RasterDataset; style: RasterStyle | undefined; disabled: boolean; onSave: (style: RasterStyle) => Promise<boolean> }) {
  const [mode, setMode] = useState<RasterStyle['mode']>('gray');
  const [bands, setBands] = useState<number[]>([]);
  const [ranges, setRanges] = useState<string[][]>([]);
  const reset = () => { setMode(style?.mode ?? 'gray'); setBands(style?.bands ?? []); setRanges(style?.ranges.map((range) => range.map(String)) ?? []); };
  const saved = JSON.stringify(style);
  useEffect(reset, [dataset.id, saved]);
  const setBand = (index: number, bandIndex: number) => {
    const band = dataset.raster.bands.find((item) => item.index === bandIndex);
    setBands((current) => current.map((value, i) => i === index ? bandIndex : value));
    setRanges((current) => current.map((range, i) => i === index ? [band?.sampleMin === null || band?.sampleMin === undefined ? '' : String(band.sampleMin), band?.sampleMax === null || band?.sampleMax === undefined ? '' : String(band.sampleMax)] : range));
  };
  const changeMode = (next: RasterStyle['mode']) => {
    setMode(next);
    const nextBands = Array.from({ length: next === 'gray' ? 1 : 3 }, (_, index) => bands[index] ?? dataset.raster.bands[Math.min(index, dataset.raster.bands.length - 1)].index);
    setBands(nextBands);
    setRanges(nextBands.map((bandIndex, index) => {
      if (ranges[index]) return ranges[index];
      const band = dataset.raster.bands.find((item) => item.index === bandIndex)!;
      return [band.sampleMin === null ? '' : String(band.sampleMin), band.sampleMax === null ? '' : String(band.sampleMax)];
    }));
  };
  const candidate: RasterStyle = { mode, bands, ranges: ranges.map((range) => [Number(range[0]), Number(range[1])]), resampling: 'nearest' };
  const valid = bands.length === (mode === 'gray' ? 1 : 3) && ranges.length === bands.length && bands.every((index) => dataset.raster.bands.some((band) => band.index === index)) && ranges.every((range) => range.length === 2 && range.every((value) => value.trim() !== '' && Number.isFinite(Number(value))) && Number(range[0]) <= Number(range[1]));
  const dirty = JSON.stringify(candidate) !== saved;
  return <div className="raster-style" aria-label="栅格显示设置">
    <div className="segmented" role="group" aria-label="栅格显示模式"><button type="button" aria-pressed={mode === 'gray'} disabled={disabled} onClick={() => changeMode('gray')}>灰度</button><button type="button" aria-pressed={mode === 'rgb'} disabled={disabled} onClick={() => changeMode('rgb')}>RGB</button></div>
    {bands.map((bandIndex, index) => {
      const channel = mode === 'gray' ? '灰度' : ['R', 'G', 'B'][index];
      const band = dataset.raster.bands.find((item) => item.index === bandIndex);
      return <div className="raster-channel" key={`${mode}-${index}`}><label className="field">{channel} 波段<select aria-label={`${channel} 波段`} value={bandIndex} disabled={disabled} onChange={(event) => setBand(index, Number(event.target.value))}>{dataset.raster.bands.map((item) => <option key={item.index} value={item.index}>{item.index}{item.description ? ` · ${item.description}` : ''}</option>)}</select></label><div className="raster-range">{['最小值', '最大值'].map((label, i) => <label className="field" key={label}>{label}<input type="number" step="any" aria-label={`${channel} ${label}`} value={ranges[index]?.[i] ?? ''} disabled={disabled} onChange={(event) => setRanges((current) => current.map((range, j) => j === index ? range.map((value, k) => k === i ? event.target.value : value) : range))} /></label>)}</div><div className="raster-sample-range"><span>抽样 {band?.sampleMin ?? '未知'} / {band?.sampleMax ?? '未知'}<small>{band?.validSamplePixels ?? 0} / {band?.sampledPixels ?? 0} 有效像元</small></span><button type="button" className="icon-button" aria-label={`${channel} 使用抽样范围`} title="使用抽样范围" disabled={disabled || band?.sampleMin == null || band?.sampleMax == null} onClick={() => setBand(index, bandIndex)}><RefreshCw size={14} /></button></div></div>;
    })}
    <div className="raster-style-footer"><span>原始值范围 · 最近邻</span><button type="button" className="icon-button" title="恢复已保存显示设置" aria-label="恢复已保存显示设置" disabled={disabled || !dirty} onClick={reset}><Undo2 size={15} /></button><button type="button" className="icon-button" title="应用栅格显示设置" aria-label="应用栅格显示设置" disabled={disabled || !dirty || !valid} onClick={() => void onSave(candidate)}><Check size={16} /></button></div>
    {!valid && <p className="inline-error" role="alert">波段范围必须为有限数值，最小值不能大于最大值。</p>}
  </div>;
}
