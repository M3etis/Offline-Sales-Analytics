/**
 * Тема графиков: приглушённая сетка, mono-подписи, янтарь только у ведущей серии.
 */

export const SERIES_COLORS = [
  'var(--chart-1)',
  'var(--chart-2)',
  'var(--chart-3)',
  'var(--chart-4)',
  'var(--chart-5)',
  'var(--chart-6)',
];

export const NEUTRAL = 'var(--chart-neutral)';
export const NEGATIVE = 'var(--negative)';

export const seriesColor = (index: number) => SERIES_COLORS[index % SERIES_COLORS.length];

export const axisTick = {
  fill: 'var(--text-muted)',
  fontSize: 12,
  fontFamily: 'IBM Plex Mono, ui-monospace, monospace',
};

export const axisProps = {
  stroke: 'var(--hairline-strong)',
  tick: axisTick,
  tickLine: false,
  axisLine: false,
} as const;

export const gridProps = {
  stroke: 'var(--chart-grid)',
  strokeDasharray: '0',
  vertical: false,
} as const;

export const tooltipCursor = { stroke: 'var(--hairline-strong)', strokeWidth: 1 };
export const barCursor = { fill: 'var(--surface-hover)' };
