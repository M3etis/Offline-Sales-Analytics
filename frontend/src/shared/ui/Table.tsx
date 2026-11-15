import { useState } from 'react';
import type { ReactNode, TableHTMLAttributes } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { IconButton } from './Button';
import { formatNumber } from '../lib/format';
import './Table.css';

interface TableProps extends TableHTMLAttributes<HTMLTableElement> {
  children: ReactNode;
  stickyHeader?: boolean;
  interactive?: boolean;
  density?: 'default' | 'compact';
  maxHeight?: number | string;
  wrapClassName?: string;
  /** Идёт обновление данных: строки приглушаются, для скринридера — aria-busy */
  loading?: boolean;
}

/**
 * Таблица-ведомость: hairline-строки, числа справа (td.num / th.num), sticky-шапка.
 */
export function Table({ children, stickyHeader = false, interactive = false, density = 'default', maxHeight, wrapClassName = '', loading = false, className = '', ...rest }: TableProps) {
  return (
    <div className={`ui-table-wrap ${wrapClassName}`} style={maxHeight ? { maxHeight } : undefined}>
      <table
        className={`ui-table ${stickyHeader ? 'ui-table--sticky' : ''} ${interactive ? 'ui-table--interactive' : ''} ${loading ? 'ui-table--loading' : ''} ui-table--${density} ${className}`}
        aria-busy={loading || undefined}
        {...rest}
      >
        {children}
      </table>
    </div>
  );
}

interface PaginationProps {
  page: number;
  pageCount: number;
  onPageChange: (page: number) => void;
  total?: number;
  pageSize?: number;
  pageSizeOptions?: number[];
  onPageSizeChange?: (size: number) => void;
  className?: string;
}

/**
 * Номер страницы, который можно ввести: на таблице в сотни страниц до нужной иначе не добраться.
 * Применяется по Enter или при уходе из поля; неверный номер возвращает текущий.
 */
function PageJump({ page, pageCount, onPageChange }: { page: number; pageCount: number; onPageChange: (page: number) => void }) {
  const [draft, setDraft] = useState(String(page));
  const [shownPage, setShownPage] = useState(page);
  if (shownPage !== page) {
    setShownPage(page);
    setDraft(String(page));
  }

  const apply = () => {
    const next = Number(draft);
    if (Number.isInteger(next) && next >= 1 && next <= pageCount && next !== page) onPageChange(next);
    else setDraft(String(page));
  };

  return (
    <span className="ui-pagination__page num">
      <input
        className="ui-pagination__input"
        type="text"
        inputMode="numeric"
        value={draft}
        size={Math.max(2, String(pageCount).length)}
        aria-label={`Номер страницы из ${pageCount}`}
        onChange={(e) => setDraft(e.target.value.replace(/\D/g, ''))}
        onBlur={apply}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            apply();
          } else if (e.key === 'Escape') {
            setDraft(String(page));
          }
        }}
      />
      <span aria-hidden="true"> / {pageCount}</span>
    </span>
  );
}

export function Pagination({ page, pageCount, onPageChange, total, pageSize, pageSizeOptions, onPageSizeChange, className = '' }: PaginationProps) {
  const safeCount = Math.max(1, pageCount);
  const from = total !== undefined && pageSize ? Math.min(total, (page - 1) * pageSize + 1) : null;
  const to = total !== undefined && pageSize ? Math.min(total, page * pageSize) : null;

  return (
    <div className={`ui-pagination ${className}`}>
      <div className="ui-pagination__info">
        {total !== undefined && from !== null && to !== null ? (
          <span className="num">
            {total === 0 ? '0' : `${formatNumber(from)}–${formatNumber(to)}`} из {formatNumber(total)}
          </span>
        ) : (
          <span className="num">
            Стр. {page} из {safeCount}
          </span>
        )}
        {pageSizeOptions && onPageSizeChange && pageSize && (
          <label className="ui-pagination__size">
            <span>По</span>
            <select value={pageSize} onChange={(e) => onPageSizeChange(Number(e.target.value))} aria-label="Строк на странице">
              {pageSizeOptions.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <div className="ui-pagination__nav">
        <IconButton size="sm" label="Предыдущая страница" icon={<ChevronLeft />} disabled={page <= 1} onClick={() => onPageChange(page - 1)} tooltip={false} />
        {safeCount > 1 ? (
          <PageJump page={page} pageCount={safeCount} onPageChange={onPageChange} />
        ) : (
          <span className="ui-pagination__page num" aria-live="polite">
            {page} / {safeCount}
          </span>
        )}
        <IconButton size="sm" label="Следующая страница" icon={<ChevronRight />} disabled={page >= safeCount} onClick={() => onPageChange(page + 1)} tooltip={false} />
      </div>
    </div>
  );
}
