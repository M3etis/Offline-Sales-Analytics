import type { ReactNode } from 'react';
import './ChartTooltip.css';

export interface PayloadEntry {
  name?: string | number;
  value?: number | string | (number | string)[];
  color?: string;
  stroke?: string;
  fill?: string;
  dataKey?: string | number;
  /** 'none' — серия помечена tooltipType="none" */
  type?: string;
  payload?: Record<string, unknown>;
}

interface ChartTooltipProps {
  active?: boolean;
  payload?: PayloadEntry[];
  label?: unknown;
  labelFormatter?: (label: unknown) => ReactNode;
  valueFormatter?: (value: number, name: string) => ReactNode;
  /**
   * Цвет ключа для конкретной точки. Recharts отдаёт в тултип цвет серии, а столбцы могут быть
   * раскрашены по отдельности (<Cell>) — тогда ключ должен совпадать с цветом наведённого столбца
   */
  colorOf?: (entry: PayloadEntry) => string | undefined;
}

/** Тултип графика: подпись периода сверху, серии с цветовым ключом и значениями справа */
export function ChartTooltip({ active, payload, label, labelFormatter, valueFormatter, colorOf }: ChartTooltipProps) {
  if (!active || !payload || payload.length === 0) return null;
  const heading = labelFormatter ? labelFormatter(label) : label !== undefined ? String(label) : null;
  // Серия с tooltipType="none" (пунктирный хвост неполного месяца) повторяет основную серию с тем же
  // именем; стандартный тултип recharts её скрывает. Оставляем её, только если основной серии в точке нет
  const entries = payload.filter(
    (entry) => entry.type !== 'none' || !payload.some((other) => other !== entry && other.type !== 'none' && other.name === entry.name),
  );
  return (
    <div className="chart-tooltip">
      {heading && <div className="chart-tooltip__label">{heading}</div>}
      {entries.map((entry, i) => {
        const raw = Array.isArray(entry.value) ? entry.value[0] : entry.value;
        const n = Number(raw);
        const name = String(entry.name ?? entry.dataKey ?? '');
        const color = colorOf?.(entry) || entry.color || entry.stroke || entry.fill;
        return (
          <div key={`${name}-${i}`} className="chart-tooltip__row">
            <span className="chart-tooltip__key" style={{ background: color }} />
            <span className="chart-tooltip__name">{name}</span>
            <span className="chart-tooltip__value">{valueFormatter && Number.isFinite(n) ? valueFormatter(n, name) : String(raw ?? '')}</span>
          </div>
        );
      })}
    </div>
  );
}
