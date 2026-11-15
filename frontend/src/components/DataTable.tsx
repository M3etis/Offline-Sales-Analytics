import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, SearchX } from 'lucide-react';
import { dataApi } from '../shared/api/data.api';
import { EmptyState, ErrorState, Pagination, SearchInput, SkeletonLines, Table, Toolbar, ToolbarSpacer } from '../shared/ui';
import { formatCount, formatIfDate, formatNumber, ROWS } from '../shared/lib/format';
import { useDebouncedValue } from '../shared/hooks/useDebouncedValue';
import './DataTable.css';

interface DataTableProps {
  datasetId?: string;
}

type Row = Record<string, string | number | null>;

/** Таблица выбранной базы: серверная сортировка, поиск и пагинация */
export default function DataTable({ datasetId }: DataTableProps) {
  const [data, setData] = useState<Row[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  // Поиск применяется через 300 мс после ввода; запрос из одного символа оставляет прежний результат
  const typedSearch = useDebouncedValue(searchQuery, 300);
  const [prevTypedSearch, setPrevTypedSearch] = useState(typedSearch);
  if (prevTypedSearch !== typedSearch) {
    setPrevTypedSearch(typedSearch);
    if (typedSearch.length !== 1) setDebouncedSearch(typedSearch);
  }
  const [sortBy, setSortBy] = useState('date');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  // Номер последнего запроса таблицы: ответ на устаревший запрос не перезаписывает свежий
  const requestRef = useRef(0);

  // Смена базы сбрасывает страницу и поиск в том же рендере — первый же запрос уходит с page=1,
  // а не со старой страницей и старым поиском (раньше это делал эффект, срабатывавший после запроса)
  const [prevDatasetId, setPrevDatasetId] = useState(datasetId);
  if (prevDatasetId !== datasetId) {
    setPrevDatasetId(datasetId);
    setPage(1);
    setSearchQuery('');
    setDebouncedSearch('');
  }

  const handleSearch = useCallback((value: string) => {
    setSearchQuery(value);
    setPage(1);
  }, []);

  const fetchData = useCallback(async () => {
    const request = ++requestRef.current;
    setLoading(true);
    setError(false);
    try {
      const res = await dataApi.getTable({
        page,
        page_size: pageSize,
        sort_by: sortBy,
        sort_order: sortOrder,
        dataset_id: datasetId || undefined,
        search: debouncedSearch || undefined,
      });
      if (request !== requestRef.current) return;
      setData(res.data as Row[]);
      if (res.columns && res.columns.length > 0) setColumns(res.columns);
      else if (res.data && res.data.length > 0) setColumns(Object.keys(res.data[0]));
      setTotal(res.total);
      setTotalPages(res.total_pages);
    } catch (e) {
      if (request !== requestRef.current) return;
      console.error(e);
      setError(true);
    } finally {
      if (request === requestRef.current) setLoading(false);
    }
  }, [page, pageSize, sortBy, sortOrder, datasetId, debouncedSearch]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleSort = (column: string) => {
    if (sortBy === column) setSortOrder((o) => (o === 'asc' ? 'desc' : 'asc'));
    else {
      setSortBy(column);
      setSortOrder('desc');
    }
    setPage(1);
  };

  const numericCols = new Set(columns.filter((c) => data.some((r) => typeof r[c] === 'number')));

  const renderCell = (value: string | number | null) => {
    if (value === null || value === undefined || value === '') return <span className="data-table__empty-cell">—</span>;
    if (typeof value === 'number') return formatNumber(value, 2);
    return formatIfDate(value) ?? value;
  };

  return (
    <div className="data-table">
      <Toolbar className="data-table__toolbar">
        <SearchInput value={searchQuery} onChange={handleSearch} placeholder="Поиск по всем колонкам" label="Поиск по таблице" className="data-table__search" />
        <ToolbarSpacer />
        <span className="data-table__count num">{loading && total === 0 ? '…' : formatCount(total, ROWS)}</span>
      </Toolbar>

      {error ? (
        <ErrorState onRetry={fetchData} />
      ) : loading && data.length === 0 ? (
        <SkeletonLines className="data-table__skeleton" widths={Array.from({ length: 10 }, (_, i) => `${92 - (i % 4) * 8}%`)} />
      ) : data.length === 0 ? (
        <EmptyState
          compact
          icon={<SearchX />}
          title={debouncedSearch ? `Ничего не найдено по запросу «${debouncedSearch}»` : 'В таблице нет строк'}
          description={debouncedSearch ? 'Проверьте написание или поищите по другой колонке.' : undefined}
        />
      ) : (
        <>
          <Table stickyHeader density="compact" maxHeight="calc(100vh - 280px)" wrapClassName="data-table__wrap" loading={loading}>
            <thead>
              <tr>
                {columns.map((col) => {
                  const active = sortBy === col;
                  return (
                    <th
                      key={col}
                      className={numericCols.has(col) ? 'num' : undefined}
                      aria-sort={active ? (sortOrder === 'asc' ? 'ascending' : 'descending') : undefined}
                    >
                      <button type="button" className="ui-table__sort" onClick={() => handleSort(col)}>
                        <span className="data-table__col-name">{col}</span>
                        {active && (sortOrder === 'asc' ? <ArrowUp aria-hidden="true" /> : <ArrowDown aria-hidden="true" />)}
                      </button>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {data.map((row, i) => (
                <tr key={`${page}-${i}`}>
                  {columns.map((col) => (
                    <td key={col} className={numericCols.has(col) ? 'num' : undefined}>
                      {renderCell(row[col])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </Table>
          <Pagination
            page={page}
            pageCount={totalPages}
            onPageChange={setPage}
            total={total}
            pageSize={pageSize}
            pageSizeOptions={[20, 50, 100, 200]}
            onPageSizeChange={(n) => {
              setPageSize(n);
              setPage(1);
            }}
          />
        </>
      )}
    </div>
  );
}
