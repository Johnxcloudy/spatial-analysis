import type { MapLayer } from '../../../../shared/contracts';
import { vectorLegendEntries, type VectorSymbol } from '../vector-style';

function SymbolSwatch({ symbol, geometryType, label }: { symbol: VectorSymbol; geometryType: string; label: string }) {
  // Enlarge the swatch for the largest supported point/outline without shrinking the symbol.
  const size = Math.max(32, Math.ceil(2 * symbol.pointRadiusPx + symbol.pointStrokeWidthPx + 4));
  const middle = size / 2;
  return <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${label} 符号`}>
    {/Point/.test(geometryType)
      ? <circle cx={middle} cy={middle} r={symbol.pointRadiusPx} fill={symbol.pointFillColor} stroke={symbol.pointStrokeColor} strokeWidth={symbol.pointStrokeWidthPx} />
      : /LineString/.test(geometryType)
        ? <line x1={3} y1={middle} x2={size - 3} y2={middle} stroke={symbol.lineColor} strokeWidth={symbol.strokeWidthPx} />
        : <rect x={5} y={7} width={size - 10} height={size - 14} fill={symbol.fillColor} stroke={symbol.strokeColor} strokeWidth={symbol.strokeWidthPx} />}
  </svg>;
}

export function VectorLegend({ layer, geometryType }: { layer: MapLayer; geometryType: string }) {
  const spec = layer.cartography;
  if (!spec || !spec.legend.visible) return null;
  return <section className="vector-legend" aria-label={`${layer.name} 已配置图例`}>
    <h3>{spec.legend.title}</h3>
    <p>已配置符号 · 不表示类别存在或完整覆盖</p>
    <ul>{vectorLegendEntries(spec).map((entry) => <li key={entry.key}>
      <span className="vector-legend-swatch" style={{ opacity: layer.opacity }}><SymbolSwatch symbol={entry.symbol} geometryType={geometryType} label={entry.label} /></span>
      <span><strong>{entry.label}</strong><small>{entry.valueLabel}</small></span>
    </li>)}</ul>
  </section>;
}
