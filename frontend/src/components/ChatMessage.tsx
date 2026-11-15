import { memo, useMemo, useState } from 'react';
import { Code2, Pause, Play, RotateCw, Square, ThumbsDown, ThumbsUp } from 'lucide-react';
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts';
import { Alert, Button, IconButton, RankedList, Table } from '../shared/ui';
import { ChartTooltip } from '../shared/charts/ChartTooltip';
import { axisProps, barCursor, gridProps, NEGATIVE, NEUTRAL, seriesColor, tooltipCursor } from '../shared/charts/chartTheme';
import { formatAxisMoney, formatDateTime, formatIfDate, formatNumber, formatPercent } from '../shared/lib/format';
import { serverColumnLabel, useColumnLabels } from '../shared/lib/columnLabels';
import './ChatMessage.css';

/**
 * Убирает разметку Markdown из ответа модели. Трогаем только саму разметку:
 * «product_name», «2 * 3» и «#1» — это данные, а не форматирование.
 */
function cleanText(text: string): string {
  return text
    .replace(/\[[^\]]*\]/g, '')
    .replace(/^([ \t]*)\*{1,2}(?=\s)/gm, '$1') // маркер списка «* пункт»
    .replace(/(?<!\s)\*{1,2}|\*{1,2}(?!\s)/g, '') // **жирный**, *курсив*; « * » между пробелами — умножение
    .replace(/^[ \t]*#{1,6}[ \t]*/gm, '') // заголовки только в начале строки
    .replace(/(?<![\p{L}\p{N}])_{1,2}|_{1,2}(?![\p{L}\p{N}])/gu, '') // _курсив_, но не snake_case
    .replace(/`/g, '')
    .trim();
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  sql?: string | null;
  data?: any[] | null;
  chartLimit?: number | null;
  chartType?: string | null;
  chartValueKey?: string | null;
  processingTime?: number | null;
  isCached?: boolean;
  cachedAt?: string;
  feedback?: 'positive' | 'negative' | null;
  retryable?: boolean;
}

interface ChatMessageProps {
  message: Message;
  onPlayAudio: (text: string) => void;
  onStopAudio?: () => void;
  onPauseAudio?: () => void;
  audioState?: { text: string | null; status: 'loading' | 'playing' | 'paused' | 'stopped' };
  showCharts?: boolean;
  onFeedback?: (messageId: string, feedback: 'positive' | 'negative' | null) => void;
  onRetry?: () => void;
}

// Запасные подписи колонок, пока не загрузились подписи бэкенда (shared/lib/columnLabels).
// Новые ключи добавлять в app/domain/ai/data_format.py, а не сюда
const KEY_TRANSLATIONS: Record<string, string> = {
  revenue: 'Выручка',
  total_revenue: 'Выручка',
  profit: 'Прибыль',
  total_profit: 'Прибыль',
  orders: 'Заказов',
  order_count: 'Заказов',
  quantity: 'Продано, шт.',
  total_quantity: 'Продано, шт.',
  sold_units: 'Продано, шт.',
  price: 'Цена',
  discount: 'Скидка',
  cost: 'Себестоимость',
  avg_check: 'Средний чек',
  value: 'Значение',
  count: 'Количество',
  total: 'Итого',
  amount: 'Сумма',
  total_sold: 'Продано',
  total_sales: 'Продажи',
  sales: 'Продажи',
  category: 'Категория',
  manager: 'Менеджер',
  product: 'Товар',
  product_name: 'Товар',
  month: 'Месяц',
  date: 'Дата',
  region: 'Регион',
  point_of_sale: 'Филиал',
  store: 'Магазин',
  manufacturer: 'Производитель',
  channel: 'Канал',
  brand: 'Бренд',
  warehouse: 'Склад',
  status: 'Статус',
  customer: 'Клиент',
  customer_id: 'Клиент',
  type: 'Тип',
  period: 'Период',
  compared_to: 'К периоду',
  weekday: 'День недели',
  days: 'Дней с данными',
  revenue_per_day: 'Выручка в день',
  margin_pct: 'Маржинальность, %',
  avg_discount: 'Средняя скидка, %',
  revenue_share_pct: 'Доля выручки, %',
  profit_share_pct: 'Доля прибыли, %',
  orders_share_pct: 'Доля заказов, %',
  quantity_share_pct: 'Доля в штуках, %',
  revenue_change_pct: 'Изменение выручки к пред. периоду, %',
  change_pct: 'Изменение, %',
  previous_value: 'Предыдущий период',
  current_value: 'Текущий период',
  cumulative_share_pct: 'Накопленная доля, %',
  abc_class: 'Класс ABC',
  returned_units: 'Возвращено, шт.',
  orders_with_returns: 'Заказов с возвратом',
  return_rate_pct: 'Доля возвратов, %',
  days_without_purchase: 'Дней без покупок',
  last_purchase: 'Последняя покупка',
  discount_range: 'Скидка, %',
  customers: 'Клиентов',
  repeat_customers: 'Повторных клиентов',
  repeat_share_pct: 'Доля повторных клиентов, %',
  one_time_customers: 'Клиентов с одной покупкой',
  orders_per_customer: 'Заказов на клиента',
  revenue_per_customer: 'Выручка на клиента',
  churned_customers: 'Клиентов в оттоке',
  products: 'Товаров',
  regions: 'Регионов',
  managers: 'Менеджеров',
  categories: 'Категорий',
  brands: 'Брендов',
  channels: 'Каналов',
  warehouses: 'Складов',
  // Проверка качества данных
  table: 'Таблица',
  check: 'Проверка',
  found: 'Найдено',
  example: 'Пример',
};

// Значения, которые бэкенд сам кладёт в данные кодом (тип находки в инсайтах). Только точное
// совпадение: «Brand» или «Status» в английском наборе — это данные, их не переводим
const VALUE_TRANSLATIONS: Record<string, string> = {
  region_decline: 'Спад в регионе',
  weak_manager: 'Слабый менеджер',
  anomaly: 'Аномалия',
};

// Колонки, которые никогда не становятся метрикой графика
const ID_COLUMNS = new Set([
  'product_id', 'category_id', 'manufacturer_id', 'customer_id',
  'order_id', 'store_id', 'purchase_id', 'delivery_id', 'id',
  'sku_code', 'barcode', 'article', 'code', 'sku',
]);

// Идентификатор по имени: «…_id», «id», «код», «номер», «артикул»
const isIdKey = (key: string) => {
  const k = key.toLowerCase();
  return ID_COLUMNS.has(k) || k.endsWith('_id') || /код|номер|артикул/.test(k);
};

// Период или дата по имени колонки — у линейного графика это ось X
const PERIOD_KEY_RE = /^(period|month|date|day|week|year|quarter|weekday|dt)$|_(date|month|day|week|year|quarter|period|at)$|^(date|month|year)_|^(период|месяц|дата|день|неделя|год|квартал)/i;

// Подписи оси X, которые лучше любой другой текстовой колонки
const LABEL_PREFERRED = ['period', 'weekday', 'product_name', 'product', 'category', 'manager', 'region', 'discount_range', 'point_of_sale', 'store', 'manufacturer', 'name'];
// Тоже подписи, но слабее читаемого названия и периода: { month, status, revenue } — помесячный ряд
// с разбивкой по статусу, а не сравнение статусов. Берём их, только если лучше ничего нет
const LABEL_FALLBACK = ['channel', 'brand', 'warehouse', 'status', 'customer', 'customer_id'];

/** Подпись ключа (имени колонки): известные имена переводим, остальные — «snake_case» → «Snake case» */
const translateKey = (key: unknown): string => {
  if (key === null || key === undefined || key === '') return '';
  // Колонки-даты бывают в сводных таблицах (по столбцу на месяц)
  const date = formatIfDate(key);
  if (date) return date;
  const lower = String(key).toLowerCase();
  const server = serverColumnLabel(lower);
  if (server) return server.charAt(0).toUpperCase() + server.slice(1);
  if (KEY_TRANSLATIONS[lower]) return KEY_TRANSLATIONS[lower];
  const text = String(key).replace(/_/g, ' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
};

/** Подпись значения (категория на оси, строка рейтинга, теплокарта): данные как есть, только даты по-русски */
const formatLabel = (value: unknown): string => {
  if (value === null || value === undefined || value === '') return '';
  const date = formatIfDate(value);
  if (date) return date;
  return VALUE_TRANSLATIONS[String(value)] ?? String(value);
};

const formatCell = (value: unknown) => formatNumber(value, 2);

const generateTicks = (min: number, max: number, count = 5) => {
  const range = Math.max(max, 0) - Math.min(min, 0);
  if (range <= 0) return [0, 25, 50, 75, 100];
  const step = range / (count - 1);
  const magnitude = Math.pow(10, Math.floor(Math.log10(step)));
  const normalizedStep = step / magnitude;
  const niceStep = normalizedStep <= 1 ? 1 : normalizedStep <= 2 ? 2 : normalizedStep <= 5 ? 5 : 10;
  const finalStep = niceStep * magnitude;
  const ticks = [0];
  let current = 0;
  while (current + finalStep <= max) {
    current += finalStep;
    ticks.push(current);
  }
  if (current < max) ticks.push(current + finalStep);
  current = 0;
  while (current - finalStep >= min) {
    current -= finalStep;
    ticks.unshift(current);
  }
  if (current > min && current - min > finalStep * 0.1) ticks.unshift(current - finalStep);
  return ticks;
};

// Колонка с одним и тем же значением во всех строках (например, `'Спад в регионе' AS type` из SQL)
// не различает строки — как подпись она бесполезна
const isConstantColumn = (rows: Record<string, unknown>[], key: string) =>
  rows.length > 1 && rows.every((r) => r[key] === rows[0][key]);

// Первое непустое значение колонки — по нему судим о её типе
const sampleOf = (rows: Record<string, unknown>[], key: string) =>
  rows.find((r) => r[key] !== null && r[key] !== undefined && r[key] !== '')?.[key];

const isPeriodColumn = (rows: Record<string, unknown>[], key: string) => {
  // «revenue_per_day» — показатель, а не день
  if (/_per_/i.test(key)) return false;
  if (PERIOD_KEY_RE.test(key)) return true;
  const v = sampleOf(rows, key);
  return typeof v === 'string' && (formatIfDate(v) !== null || /^\d{4}-(\d{2}|Q[1-4])$/i.test(v.trim()));
};

// Идентификатор: по имени или по значению (число, строка из одних цифр вроде «000123»)
const isIdColumn = (rows: Record<string, unknown>[], key: string) => {
  if (isIdKey(key)) return true;
  const v = sampleOf(rows, key);
  return typeof v === 'number' || (typeof v === 'string' && /^\d+$/.test(v.trim()));
};

// Читаемое название: строка, не дата и не код
const isTextColumn = (rows: Record<string, unknown>[], key: string) =>
  typeof sampleOf(rows, key) === 'string' && !isPeriodColumn(rows, key) && !isIdColumn(rows, key);

/**
 * Колонка для подписей оси X и строк рейтинга. Порядок: у линейного графика — период; затем известные
 * названия (товар, категория, регион…), любое читаемое текстовое название, период, слабые подписи
 * (статус, канал, бренд…) и только потом идентификатор. Имя клиента важнее его ID: строки
 * { customer_id: 1042, name: 'ТОО Ромашка' } подписываются именем
 */
const pickCategoryKey = (rows: Record<string, unknown>[], chartType?: string | null) => {
  const tiers: ((k: string) => boolean)[] = [
    (k) => chartType === 'line' && isPeriodColumn(rows, k),
    (k) => LABEL_PREFERRED.includes(k) && !isIdColumn(rows, k),
    (k) => isTextColumn(rows, k) && !LABEL_FALLBACK.includes(k),
    (k) => isPeriodColumn(rows, k),
    (k) => LABEL_FALLBACK.includes(k) && !isIdColumn(rows, k),
    (k) => typeof sampleOf(rows, k) === 'string',
    (k) => isIdKey(k),
  ];
  const pick = (cands: string[]) => {
    for (const tier of tiers) {
      const found = cands.find(tier);
      if (found) return found;
    }
    return undefined;
  };
  const keys = Object.keys(rows[0]);
  // Сначала среди колонок, где значения различаются: одинаковая во всех строках подпись бесполезна
  return pick(keys.filter((k) => !isConstantColumn(rows, k))) || pick(keys) || keys[0];
};

function SeriesLegend({ keys }: { keys: string[] }) {
  if (keys.length < 2) return null;
  return (
    <div className="chat-chart__legend">
      {keys.map((k, i) => (
        <span key={k} className="chat-chart__key">
          <span className="chat-chart__swatch" style={{ background: seriesColor(i) }} />
          {translateKey(k)}
        </span>
      ))}
    </div>
  );
}

export default memo(function ChatMessage({ message, onPlayAudio, onStopAudio, onPauseAudio, audioState, showCharts = true, onFeedback, onRetry }: ChatMessageProps) {
  const isUser = message.role === 'user';
  const [sqlExpanded, setSqlExpanded] = useState(false);
  // Подписи колонок с бэкенда: пришли — пересобрать график и таблицу с ними
  const labelsVersion = useColumnLabels();

  const chart = useMemo(() => {
    if (!showCharts) return null;
    const { chartType, data } = message;
    if (!data || data.length === 0 || !chartType) return null;
    if (data.length === 1 && Object.keys(data[0]).length > 2) return null;

    if (chartType === 'line' || chartType === 'bar') {
      const rows = chartType === 'bar' ? data.slice(0, message.chartLimit || 20) : data;
      const categoryKey = pickCategoryKey(rows, chartType);
      const numericKeys = Object.keys(rows[0]).filter((k) => k !== categoryKey && typeof rows[0][k] === 'number' && !isIdKey(k));
      // Готовые расчёты отдают в строке много показателей разного масштаба (выручка, заказы, маржа, %):
      // на одном графике они нечитаемы — рисуем показатель, о котором спросили
      const keys = message.chartValueKey && numericKeys.includes(message.chartValueKey) ? [message.chartValueKey] : numericKeys;
      if (keys.length === 0) return null;
      const flat = rows.flatMap((d: any) => keys.map((k) => Number(d[k]) || 0));
      const minY = Math.min(...flat);
      const maxY = Math.max(...flat);
      const ticks = generateTicks(minY, maxY);
      const domain: [number, number] = [minY < 0 ? minY * 1.1 : 0, ticks[ticks.length - 1]];
      const tooltip = <ChartTooltip labelFormatter={(l) => formatLabel(l)} valueFormatter={(v) => formatCell(v)} />;

      if (chartType === 'line') {
        return (
          <div className="chat-chart">
            <SeriesLegend keys={keys} />
            <ResponsiveContainer width="100%" height={240} minWidth={0}>
              <LineChart data={rows} margin={{ top: 12, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid {...gridProps} />
                <XAxis dataKey={categoryKey} {...axisProps} tickFormatter={formatLabel} tickMargin={8} minTickGap={16} />
                <YAxis {...axisProps} width={64} tickFormatter={formatAxisMoney} domain={domain} allowDataOverflow />
                <Tooltip cursor={tooltipCursor} content={tooltip} />
                {keys.map((key, i) => (
                  <Line
                    key={key}
                    type="monotone"
                    dataKey={key}
                    name={translateKey(key)}
                    stroke={seriesColor(i)}
                    strokeWidth={i === 0 ? 2 : 1.5}
                    dot={false}
                    activeDot={{ r: 3, strokeWidth: 0 }}
                    isAnimationActive={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        );
      }

      const single = keys.length === 1;
      const maxIndex = single ? rows.reduce((best: number, r: any, i: number) => (Number(r[keys[0]]) > Number(rows[best][keys[0]]) ? i : best), 0) : -1;
      // Цвет столбца: отрицательные — красные, у одиночной серии янтарный только максимум. Один расчёт
      // для <Cell> и для ключа в тултипе, иначе тултип показывает цвет серии, а не наведённого столбца
      const isMaxRow = (row: any) => {
        const index = rows.indexOf(row);
        // recharts может передать в тултип копию строки — тогда сравниваем по значению
        return index >= 0 ? index === maxIndex : Number(row[keys[0]]) === Number(rows[maxIndex]?.[keys[0]]);
      };
      const barFill = (row: any, key: string, seriesIndex: number) => {
        const v = Number(row[key]);
        return v < 0 ? NEGATIVE : single ? (isMaxRow(row) ? seriesColor(0) : NEUTRAL) : seriesColor(seriesIndex);
      };
      const barTooltip = (
        <ChartTooltip
          labelFormatter={(l) => formatLabel(l)}
          valueFormatter={(v) => formatCell(v)}
          colorOf={(entry) => {
            const key = String(entry.dataKey ?? '');
            return entry.payload ? barFill(entry.payload, key, keys.indexOf(key)) : undefined;
          }}
        />
      );
      return (
        <div className="chat-chart">
          <SeriesLegend keys={keys} />
          <ResponsiveContainer width="100%" height={240} minWidth={0}>
            <BarChart data={rows} margin={{ top: 12, right: 8, left: 0, bottom: 0 }} barCategoryGap="24%">
              <CartesianGrid {...gridProps} />
              <XAxis dataKey={categoryKey} {...axisProps} tickFormatter={formatLabel} tickMargin={8} interval="preserveStartEnd" minTickGap={8} />
              <YAxis {...axisProps} width={64} tickFormatter={formatAxisMoney} domain={domain} ticks={ticks} />
              <Tooltip cursor={barCursor} content={barTooltip} />
              {keys.map((key, i) => (
                <Bar key={key} dataKey={key} name={translateKey(key)} fill={seriesColor(i)} radius={[3, 3, 0, 0]} maxBarSize={40} isAnimationActive={false}>
                  {rows.map((entry: any, index: number) => (
                    <Cell key={`cell-${index}`} fill={barFill(entry, key, i)} />
                  ))}
                </Bar>
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      );
    }

    if (chartType === 'pie') {
      // Доля — ранжированный список с полосами вместо круговой диаграммы
      const categoryKey = pickCategoryKey(data, chartType);
      const numKeys = Object.keys(data[0]).filter((k) => k !== categoryKey && typeof data[0][k] === 'number' && !isIdKey(k));
      const isRevenueCol = (k: string) => {
        const lk = k.toLowerCase();
        return lk === 'revenue' || lk === 'sales_amount' || lk === 'total_sales_amount' || lk === 'total_revenue' || lk.endsWith('_revenue') || lk.endsWith('_amount');
      };
      const isCountCol = (k: string) => {
        const lk = k.toLowerCase();
        return lk === 'sales_count' || lk === 'count' || lk === 'order_count' || lk.endsWith('_count') || lk.includes('quantity');
      };
      const valueKey =
        (message.chartValueKey && numKeys.includes(message.chartValueKey) ? message.chartValueKey : null) ||
        numKeys.find(isRevenueCol) ||
        numKeys.find((k) => !isCountCol(k)) ||
        numKeys[0];
      if (!valueKey) return null;
      const total = data.reduce((s: number, r: any) => s + Number(r[valueKey] || 0), 0);
      const sorted = [...data].sort((a: any, b: any) => Number(b[valueKey]) - Number(a[valueKey]));
      return (
        <div className="chat-chart chat-chart--share">
          <p className="chat-chart__caption">Доля: {translateKey(valueKey).toLowerCase()}</p>
          <RankedList
            items={sorted.map((r: any, i: number) => ({
              key: `${r[categoryKey]}-${i}`,
              label: formatLabel(r[categoryKey]),
              value: Number(r[valueKey]) || 0,
              meta: total > 0 ? formatPercent((Number(r[valueKey]) / total) * 100) : undefined,
              valueLabel: formatCell(r[valueKey]),
            }))}
          />
        </div>
      );
    }

    if (chartType === 'scatter') {
      const numKeys = Object.keys(data[0]).filter((k) => typeof data[0][k] === 'number' && !isIdKey(k));
      if (numKeys.length < 2) return null;
      const [xKey, yKey] = numKeys;
      return (
        <div className="chat-chart">
          <ResponsiveContainer width="100%" height={260} minWidth={0}>
            <ScatterChart margin={{ top: 12, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid {...gridProps} vertical />
              <XAxis dataKey={xKey} name={translateKey(xKey)} {...axisProps} tickFormatter={formatAxisMoney} />
              <YAxis dataKey={yKey} name={translateKey(yKey)} {...axisProps} width={64} tickFormatter={formatAxisMoney} />
              <Tooltip cursor={tooltipCursor} content={<ChartTooltip valueFormatter={(v) => formatCell(v)} />} />
              <Scatter data={data} fill={seriesColor(0)} fillOpacity={0.8} isAnimationActive={false} />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      );
    }

    if (chartType === 'heatmap') {
      const keys = Object.keys(data[0]);
      const strKeys = keys.filter((k) => typeof data[0][k] === 'string' && !isConstantColumn(data, k));
      const numKeys = keys.filter((k) => typeof data[0][k] === 'number' && !isIdKey(k));
      if (strKeys.length < 2 || numKeys.length < 1) return null;
      const [rowKey, colKey] = strKeys;
      const valKey = numKeys[0];
      const rowLabels = [...new Set(data.map((d: any) => d[rowKey]))];
      const colLabels = [...new Set(data.map((d: any) => d[colKey]))];
      const maxVal = Math.max(...data.map((d: any) => Number(d[valKey]) || 0), 1);
      return (
        <div className="chat-chart">
          <div className="heatmap" style={{ gridTemplateColumns: `minmax(96px, max-content) repeat(${colLabels.length}, minmax(56px, 1fr))` }}>
            <div />
            {colLabels.map((c) => (
              <div key={String(c)} className="heatmap__label heatmap__label--col" title={String(c)}>
                {formatLabel(c)}
              </div>
            ))}
            {rowLabels.map((r) => (
              <div key={String(r)} className="heatmap__row">
                <div className="heatmap__label" title={String(r)}>
                  {formatLabel(r)}
                </div>
                {colLabels.map((c) => {
                  const item = data.find((d: any) => d[rowKey] === r && d[colKey] === c);
                  const val = item ? Number(item[valKey]) || 0 : 0;
                  const pct = Math.round((val / maxVal) * 85);
                  return (
                    <div
                      key={`${r}-${c}`}
                      className="heatmap__cell"
                      style={{ background: `color-mix(in srgb, var(--chart-1) ${pct}%, var(--surface-raised))`, color: pct > 50 ? 'var(--on-accent)' : undefined }}
                      title={`${r} × ${c}: ${formatCell(val)}`}
                    >
                      {val > 0 ? formatAxisMoney(val) : ''}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      );
    }

    return null;
  }, [message, showCharts, labelsVersion]);

  const table = useMemo(() => {
    const data = message.data;
    if (!data || data.length === 0 || chart) return null;
    if (data.length === 1 && Object.keys(data[0]).length > 3) return null;
    const columns = Object.keys(data[0]);
    const isNumericCol = (col: string) => data.some((row: any) => typeof row[col] === 'number');
    const numeric = new Set(columns.filter(isNumericCol));
    return (
      <div className="chat-sheet">
        <Table density="compact" stickyHeader maxHeight={360}>
          <thead>
            <tr>
              <th className="num chat-sheet__index">#</th>
              {columns.map((col) => (
                <th key={col} className={numeric.has(col) ? 'num' : undefined}>
                  {translateKey(col)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.map((row: any, i: number) => (
              <tr key={i}>
                <td className="num chat-sheet__index">{i + 1}</td>
                {columns.map((col) => {
                  const v = row[col];
                  if (typeof v === 'number') {
                    return (
                      <td key={col} className="num">
                        {formatCell(v)}
                      </td>
                    );
                  }
                  if (v === null || v === undefined) {
                    return (
                      <td key={col} className={numeric.has(col) ? 'num muted' : 'muted'}>
                        —
                      </td>
                    );
                  }
                  return <td key={col}>{formatIfDate(v) ?? String(v)}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </Table>
      </div>
    );
  }, [message.data, chart, labelsVersion]);

  if (isUser) {
    return (
      <div className="chat-message chat-message--user">
        <div className="chat-message__bubble">{message.text}</div>
      </div>
    );
  }

  if (message.retryable) {
    return (
      <div className="chat-message chat-message--assistant">
        <Alert
          tone="negative"
          title="Не удалось получить ответ"
          action={
            onRetry ? (
              <Button size="sm" icon={<RotateCw />} onClick={onRetry}>
                Повторить
              </Button>
            ) : undefined
          }
        >
          {cleanText(message.text)}
        </Alert>
      </div>
    );
  }

  const isThisAudio = audioState?.text === message.text;
  const isPlaying = isThisAudio && audioState?.status === 'playing';
  const isPaused = isThisAudio && audioState?.status === 'paused';
  const isAudioLoading = isThisAudio && audioState?.status === 'loading';

  return (
    <div className="chat-message chat-message--assistant">
      <div className="chat-message__text">
        {cleanText(message.text)}
      </div>

      {chart && <div className="chat-sheet chat-sheet--chart">{chart}</div>}
      {table}

      {message.sql && sqlExpanded && <pre className="chat-message__sql">{message.sql}</pre>}

      <div className="chat-message__footer">
        <div className="chat-message__actions">
          {isPlaying ? (
            onPauseAudio && <IconButton size="sm" label="Пауза" icon={<Pause />} onClick={onPauseAudio} />
          ) : (
            <IconButton size="sm" label={isPaused ? 'Продолжить' : 'Прослушать'} icon={<Play />} onClick={() => onPlayAudio(message.text)} />
          )}
          {(isPlaying || isPaused || isAudioLoading) && onStopAudio && <IconButton size="sm" label="Остановить" icon={<Square />} onClick={onStopAudio} />}
          {onFeedback && (
            <>
              <IconButton
                size="sm"
                label="Ответ полезен"
                icon={<ThumbsUp />}
                active={message.feedback === 'positive'}
                className={message.feedback === 'positive' ? 'chat-message__vote--up' : ''}
                onClick={() => onFeedback(message.id, message.feedback === 'positive' ? null : 'positive')}
              />
              <IconButton
                size="sm"
                label="Ответ не полезен"
                icon={<ThumbsDown />}
                active={message.feedback === 'negative'}
                className={message.feedback === 'negative' ? 'chat-message__vote--down' : ''}
                onClick={() => onFeedback(message.id, message.feedback === 'negative' ? null : 'negative')}
              />
            </>
          )}
          {message.sql && (
            <IconButton size="sm" label={sqlExpanded ? 'Скрыть SQL-запрос' : 'Показать SQL-запрос'} icon={<Code2 />} active={sqlExpanded} onClick={() => setSqlExpanded((v) => !v)} />
          )}
        </div>
        {message.processingTime ? (
          <span className="chat-message__meta">
            {formatNumber(message.processingTime, 1)} с{message.isCached && message.cachedAt ? ` · из кэша от ${formatDateTime(message.cachedAt)}` : ''}
          </span>
        ) : null}
      </div>
    </div>
  );
});
