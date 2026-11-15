import type { ReactNode } from 'react';
import { formatChange } from '../lib/format';
import { Skeleton } from './Feedback';
import './Data.css';

interface StatProps {
  label: ReactNode;
  value: ReactNode;
  /** Точное значение для подсказки */
  exact?: string;
  change?: number | null;
  changeLabel?: string;
  loading?: boolean;
  emphasis?: boolean;
}

/** Показатель: подпись, число, изменение с базой сравнения */
export function Stat({ label, value, exact, change, changeLabel = 'к прошлому периоду', loading = false, emphasis = false }: StatProps) {
  const hasChange = change !== undefined && change !== null && Number.isFinite(change);
  const tone = !hasChange || change === 0 ? 'flat' : (change as number) > 0 ? 'up' : 'down';
  return (
    <div className={`ui-stat ${emphasis ? 'ui-stat--emphasis' : ''}`}>
      <span className="ui-stat__label">{label}</span>
      {loading ? (
        <Skeleton width="60%" height={32} />
      ) : (
        <span className="ui-stat__value" title={exact}>
          {value}
        </span>
      )}
      {!loading && hasChange && (
        <span className={`ui-stat__change ui-stat__change--${tone}`}>
          <span className="num">{formatChange(change)}</span> {changeLabel}
        </span>
      )}
    </div>
  );
}

export interface RankedItem {
  key: string;
  label: ReactNode;
  value: number;
  valueLabel: ReactNode;
  meta?: ReactNode;
  title?: string;
}

interface RankedListProps {
  items: RankedItem[];
  selectedKey?: string | null;
  onSelect?: (key: string) => void;
  loading?: boolean;
  rows?: number;
  ariaLabel?: string;
}

/** Ранжированный список с тонкой полосой доли: категории, топ товаров */
export function RankedList({ items, selectedKey, onSelect, loading = false, rows = 6, ariaLabel }: RankedListProps) {
  if (loading) {
    return (
      <div className="ui-ranked" aria-busy="true">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="ui-ranked__skeleton">
            <Skeleton width={`${70 - i * 6}%`} height={14} />
            <Skeleton width={56} height={14} />
          </div>
        ))}
      </div>
    );
  }

  const max = Math.max(...items.map((i) => Math.abs(i.value)), 0) || 1;
  const interactive = Boolean(onSelect);

  return (
    <ol className={`ui-ranked ${interactive ? 'ui-ranked--interactive' : ''} ${selectedKey ? 'has-selection' : ''}`} aria-label={ariaLabel}>
      {items.map((item, index) => {
        const selected = selectedKey === item.key;
        const width = `${Math.max(2, (Math.abs(item.value) / max) * 100)}%`;
        const content = (
          <>
            <span className="ui-ranked__rank num">{index + 1}</span>
            <span className="ui-ranked__main">
              <span className="ui-ranked__row">
                <span className="ui-ranked__label" title={item.title}>
                  {item.label}
                </span>
                {item.meta && <span className="ui-ranked__meta num">{item.meta}</span>}
                <span className="ui-ranked__value num">{item.valueLabel}</span>
              </span>
              <span className="ui-ranked__track" aria-hidden="true">
                <span className="ui-ranked__bar" style={{ width }} />
              </span>
            </span>
          </>
        );
        return (
          <li key={item.key} className={`ui-ranked__item ${selected ? 'is-selected' : ''}`}>
            {interactive ? (
              <button type="button" className="ui-ranked__button" aria-pressed={selected} onClick={() => onSelect?.(item.key)}>
                {content}
              </button>
            ) : (
              <div className="ui-ranked__button">{content}</div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
