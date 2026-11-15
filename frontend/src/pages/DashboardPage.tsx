import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Database, Layers, MessageSquare, X } from 'lucide-react';
import { analyticsApi } from '../shared/api/analytics.api';
import type { CategoryData, KpiData, TimelineData } from '../shared/api/analytics.api';
import { useDataset } from '../shared/providers/DatasetContext';
import { useCanOpen } from '../components/shell/useCanOpen';
import { Button, EmptyState, ErrorState, Page, PageHeader, Panel, RankedList, Skeleton, Stat } from '../shared/ui';
import { ChartTooltip } from '../shared/charts/ChartTooltip';
import { axisProps, gridProps, tooltipCursor } from '../shared/charts/chartTheme';
import { formatAxisMoney, formatExact, formatMoney, formatMonthLong, formatMonthTick, formatMonthYear, formatNumber, formatPercent, parseDate } from '../shared/lib/format';
import { groupLabel } from '../shared/lib/datasetLabel';
import './DashboardPage.css';

interface TopProduct {
  product: string;
  category?: string;
  revenue: number;
  quantity?: number;
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const canOpen = useCanOpen();
  const { selectedDatasetId, activeGroup, currentGroup, groups, groupsLoading, groupsError, reloadGroups } = useDataset();

  const [kpis, setKpis] = useState<KpiData | null>(null);
  const [timeline, setTimeline] = useState<TimelineData[]>([]);
  const [categories, setCategories] = useState<CategoryData[]>([]);
  const [topProducts, setTopProducts] = useState<TopProduct[]>([]);
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [topLoading, setTopLoading] = useState(true);
  const [topError, setTopError] = useState(false);
  const [topAttempt, setTopAttempt] = useState(0);
  const [error, setError] = useState(false);
  const [partError, setPartError] = useState({ timeline: false, categories: false });

  const isGroupMode = Boolean(activeGroup);
  // Номер последней загрузки показателей: ответ для предыдущей базы не перезаписывает текущую
  const loadRef = useRef(0);

  // Смена базы сбрасывает категорию в том же рендере — топ товаров сразу запрашивается без
  // категории прошлой базы (раньше сброс шёл в эффекте после запроса)
  const [prevDatasetId, setPrevDatasetId] = useState(selectedDatasetId);
  if (prevDatasetId !== selectedDatasetId) {
    setPrevDatasetId(selectedDatasetId);
    setSelectedCategory(null);
  }

  const load = useCallback(() => {
    if (!selectedDatasetId || isGroupMode) return;
    const request = ++loadRef.current;
    setLoading(true);
    setError(false);
    setPartError({ timeline: false, categories: false });
    const params = { dataset_id: selectedDatasetId };
    // allSettled: сбой графика или категорий (например, 500 для базы без колонки прибыли)
    // не прячет показатели, которые загрузились, — ошибка показывается в своей панели
    Promise.allSettled([analyticsApi.getKpis(params), analyticsApi.getTimeline(params), analyticsApi.getCategories(params)])
      .then(([k, t, c]) => {
        if (request !== loadRef.current) return;
        if (k.status === 'fulfilled') setKpis(k.value);
        else {
          console.error('Dashboard KPIs failed', k.reason);
          setError(true);
        }
        if (t.status === 'fulfilled') setTimeline(t.value);
        else {
          console.error('Dashboard timeline failed', t.reason);
          setTimeline([]);
        }
        if (c.status === 'fulfilled') setCategories(c.value);
        else {
          console.error('Dashboard categories failed', c.reason);
          setCategories([]);
        }
        setPartError({ timeline: t.status === 'rejected', categories: c.status === 'rejected' });
      })
      .finally(() => {
        if (request === loadRef.current) setLoading(false);
      });
  }, [selectedDatasetId, isGroupMode]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!selectedDatasetId || isGroupMode) return;
    // Ответ для прошлой базы или категории приходит после смены — его отбрасываем
    let stale = false;
    setTopLoading(true);
    setTopError(false);
    const params: Record<string, unknown> = { dataset_id: selectedDatasetId, limit: 8 };
    if (selectedCategory) params.category = selectedCategory;
    analyticsApi
      .getTopProducts(params)
      .then((data: TopProduct[]) => {
        if (!stale) setTopProducts(data);
      })
      .catch((err) => {
        if (stale) return;
        console.error('Top products failed', err);
        // не оставляем на экране товары предыдущей базы или категории
        setTopProducts([]);
        setTopError(true);
      })
      .finally(() => {
        if (!stale) setTopLoading(false);
      });
    return () => {
      stale = true;
    };
  }, [selectedCategory, selectedDatasetId, isGroupMode, topAttempt]);

  // Хвост текущего (неполного) месяца рисуем пунктиром
  const chartData = useMemo(() => {
    if (timeline.length === 0) return [];
    const last = parseDate(timeline[timeline.length - 1].period);
    const now = new Date();
    const partial = Boolean(last && last.getFullYear() === now.getFullYear() && last.getMonth() === now.getMonth() && timeline.length > 1);
    return timeline.map((row, i) => {
      const isLast = i === timeline.length - 1;
      const isPrev = i === timeline.length - 2;
      return {
        ...row,
        revenueSolid: partial && isLast ? null : row.revenue,
        profitSolid: partial && isLast ? null : row.profit,
        revenueTail: partial && (isLast || isPrev) ? row.revenue : null,
        profitTail: partial && (isLast || isPrev) ? row.profit : null,
        partial: partial && isLast,
      };
    });
  }, [timeline]);

  const periodText = timeline.length > 0 ? `${formatMonthYear(timeline[0].period)} — ${formatMonthYear(timeline[timeline.length - 1].period)}` : null;
  const datasetName = currentGroup ? groupLabel(currentGroup) : null;
  const description = [datasetName, periodText].filter(Boolean).join(' · ') || 'Ключевые показатели выбранной базы';

  const categoryTotal = categories.reduce((s, c) => s + Number(c.revenue), 0);

  // Нет загруженных баз
  if (!groupsLoading && !groupsError && groups.length === 0) {
    return (
      <Page>
        <PageHeader title="Продажи" />
        <Panel>
          <EmptyState
            icon={<Database />}
            title="Нет данных для дашборда"
            description="Загрузите выгрузку продаж в CSV, Excel или DuckDB — показатели и графики появятся здесь."
            action={
              canOpen('/settings/data') ? (
                <Button variant="primary" onClick={() => navigate('/settings/data')}>
                  Загрузить данные
                </Button>
              ) : undefined
            }
          />
        </Panel>
      </Page>
    );
  }

  if (groupsError) {
    return (
      <Page>
        <PageHeader title="Продажи" />
        <Panel>
          <ErrorState onRetry={reloadGroups} />
        </Panel>
      </Page>
    );
  }

  if (isGroupMode && activeGroup) {
    return (
      <Page>
        <PageHeader title="Продажи" description={groupLabel(activeGroup)} />
        <Panel>
          <EmptyState
            icon={<Layers />}
            title="Дашборд строится по одной таблице"
            description={`База «${activeGroup.source_file}» состоит из ${activeGroup.tables.length} связанных таблиц. Спросите ассистента — он сам объединит таблицы и посчитает показатели.`}
            action={
              canOpen('/analyst') ? (
                <Button variant="primary" icon={<MessageSquare />} onClick={() => navigate('/analyst')}>
                  Спросить ассистента
                </Button>
              ) : undefined
            }
          />
        </Panel>
      </Page>
    );
  }

  return (
    <Page className="dashboard">
      <PageHeader title="Продажи" description={description} />

      {error ? (
        <Panel>
          <ErrorState onRetry={load} />
        </Panel>
      ) : (
        <>
          <section className="dashboard__stats" aria-label="Ключевые показатели">
            <Stat
              label="Выручка"
              loading={loading}
              value={kpis ? formatMoney(kpis.total_revenue) : '—'}
              exact={kpis ? formatExact(kpis.total_revenue) : undefined}
              change={kpis?.revenue_change}
            />
            <Stat
              label="Прибыль"
              loading={loading}
              value={kpis ? formatMoney(kpis.total_profit) : '—'}
              exact={kpis ? formatExact(kpis.total_profit) : undefined}
              change={kpis?.profit_change}
            />
            <Stat label="Заказы" loading={loading} value={kpis ? formatNumber(kpis.order_count) : '—'} change={kpis?.orders_change} />
            <Stat
              label="Средний чек"
              loading={loading}
              value={kpis ? formatMoney(kpis.avg_check) : '—'}
              exact={kpis ? formatExact(kpis.avg_check) : undefined}
            />
          </section>

          <Panel
            className="dashboard__chart-panel"
            title="Выручка и прибыль по месяцам"
            actions={
              <div className="dashboard__legend" aria-hidden="true">
                <span className="dashboard__key dashboard__key--revenue">Выручка</span>
                <span className="dashboard__key dashboard__key--profit">Прибыль</span>
              </div>
            }
          >
            <div className="dashboard__chart">
              {loading ? (
                <Skeleton height="100%" radius={8} />
              ) : partError.timeline ? (
                <ErrorState compact onRetry={load} />
              ) : chartData.length === 0 ? (
                <EmptyState compact title="Нет данных по датам" description="В таблице не найдена колонка с датой продажи." />
              ) : (
                <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                  <ComposedChart data={chartData} margin={{ top: 16, right: 20, left: 0, bottom: 0 }}>
                    <defs>
                      <linearGradient id="revenueFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.22} />
                        <stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid {...gridProps} />
                    <XAxis dataKey="period" {...axisProps} tickFormatter={formatMonthTick} tickMargin={10} minTickGap={24} />
                    <YAxis {...axisProps} tickFormatter={formatAxisMoney} width={72} tickMargin={8} />
                    <Tooltip
                      cursor={tooltipCursor}
                      content={
                        <ChartTooltip
                          labelFormatter={(label) => {
                            const row = chartData.find((r) => r.period === label);
                            return `${formatMonthLong(String(label))}${row?.partial ? ' · неполный месяц' : ''}`;
                          }}
                          valueFormatter={(v) => formatExact(Math.round(v))}
                        />
                      }
                    />
                    <Area
                      type="monotone"
                      dataKey="revenueSolid"
                      name="Выручка"
                      stroke="var(--chart-1)"
                      strokeWidth={2}
                      fill="url(#revenueFill)"
                      dot={false}
                      activeDot={{ r: 4, strokeWidth: 0, fill: 'var(--chart-1)' }}
                      connectNulls={false}
                      isAnimationActive={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="revenueTail"
                      name="Выручка"
                      stroke="var(--chart-1)"
                      strokeWidth={2}
                      strokeDasharray="4 4"
                      dot={false}
                      activeDot={{ r: 4, strokeWidth: 0, fill: 'var(--chart-1)' }}
                      legendType="none"
                      tooltipType="none"
                      isAnimationActive={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="profitSolid"
                      name="Прибыль"
                      stroke="var(--chart-2)"
                      strokeWidth={1.5}
                      dot={false}
                      activeDot={{ r: 3, strokeWidth: 0, fill: 'var(--chart-2)' }}
                      isAnimationActive={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="profitTail"
                      name="Прибыль"
                      stroke="var(--chart-2)"
                      strokeWidth={1.5}
                      strokeDasharray="4 4"
                      dot={false}
                      activeDot={false}
                      legendType="none"
                      tooltipType="none"
                      isAnimationActive={false}
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              )}
            </div>
            {!loading && chartData.some((r) => r.partial) && <p className="dashboard__note">Пунктир — текущий месяц, данные неполные</p>}
          </Panel>

          <div className="dashboard__grid">
            <Panel title="Категории" description="Доля в выручке. Нажмите, чтобы увидеть товары категории">
              {!loading && partError.categories ? (
                <ErrorState compact onRetry={load} />
              ) : !loading && categories.length === 0 ? (
                <EmptyState compact title="Нет категорий" description="В таблице не найдена колонка с категорией товара." />
              ) : (
                <RankedList
                  ariaLabel="Категории по выручке"
                  loading={loading}
                  selectedKey={selectedCategory}
                  onSelect={(key) => setSelectedCategory((prev) => (prev === key ? null : key))}
                  items={categories.map((c) => ({
                    key: c.category,
                    label: c.category,
                    value: Number(c.revenue),
                    meta: formatPercent(c.share ?? (categoryTotal ? (Number(c.revenue) / categoryTotal) * 100 : 0)),
                    valueLabel: <span title={formatExact(c.revenue)}>{formatMoney(c.revenue)}</span>,
                  }))}
                />
              )}
            </Panel>

            <Panel
              title={selectedCategory ? `Товары: ${selectedCategory}` : 'Топ товаров'}
              description="По выручке за весь период"
              actions={
                selectedCategory ? (
                  <Button size="sm" variant="ghost" icon={<X />} onClick={() => setSelectedCategory(null)}>
                    Все категории
                  </Button>
                ) : undefined
              }
            >
              {!topLoading && topError ? (
                <ErrorState compact onRetry={() => setTopAttempt((n) => n + 1)} />
              ) : !topLoading && topProducts.length === 0 ? (
                <EmptyState compact title="Нет товаров" description="Для выбранной категории продаж не найдено." />
              ) : (
                <RankedList
                  ariaLabel="Товары по выручке"
                  loading={topLoading}
                  items={topProducts.map((p) => ({
                    // Сервер группирует по паре (товар, категория): одно название может встретиться дважды
                    key: `${p.product}\u0000${p.category ?? ''}`,
                    label: p.product,
                    title: p.category ? `${p.product} · ${p.category}` : p.product,
                    value: Number(p.revenue),
                    meta: p.quantity !== undefined ? `${formatNumber(p.quantity)} шт.` : undefined,
                    valueLabel: <span title={formatExact(p.revenue)}>{formatMoney(p.revenue)}</span>,
                  }))}
                />
              )}
            </Panel>
          </div>
        </>
      )}
    </Page>
  );
}
