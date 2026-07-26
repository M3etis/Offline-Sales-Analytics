import { memo, useState } from "react";
import { Bot, User, PlayCircle, StopCircle, PauseCircle, ThumbsUp, ThumbsDown, ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  LineChart,
  Line,
  PieChart,
  Pie,
  ScatterChart,
  Scatter,
  Cell,
  XAxis,
  YAxis,

  Tooltip,
  CartesianGrid,
  Legend,
} from "recharts";
import { Tooltip as ReactTooltip } from "react-tooltip";
import "react-tooltip/dist/react-tooltip.css";
import "./ChatMessage.css";

function cleanText(text: string): string {
  return text
    .replace(/\[[^\]]*\]/g, '')
    .replace(/\*{1,2}/g, '')
    .replace(/#{1,6}\s*/g, '')
    .replace(/_{1,2}/g, '')
    .replace(/`/g, '')
    .trim();
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  sql?: string | null;
  data?: any[] | null;
  chartLimit?: number | null;
  chartType?: string | null;
  chartValueKey?: string | null;
  processingTime?: number | null;
  responseMode?: "text" | "both" | "voice";
  isCached?: boolean;
  cachedAt?: string;
  feedback?: "positive" | "negative" | null;
  retryable?: boolean;
}

interface ChatMessageProps {
  message: Message;
  onPlayAudio: (text: string) => void;
  onStopAudio?: () => void;
  onPauseAudio?: () => void;
  audioState?: { text: string | null; status: "playing" | "paused" | "stopped" };
  showCharts?: boolean;
  onFeedback?: (messageId: string, feedback: "positive" | "negative" | null) => void;
  onRetry?: () => void;
}

const COLORS = [
  "#6366f1", "#ec4899", "#14b8a6", "#f59e0b", "#8b5cf6", 
  "#f43f5e", "#06b6d4", "#10b981", "#d946ef", "#f97316", 
  "#0ea5e9", "#84cc16", "#3b82f6", "#eab308", "#ef4444", "#64748b"
];

const KEY_TRANSLATIONS: Record<string, string> = {
  revenue: "Выручка",
  profit: "Прибыль",
  orders: "Заказы",
  quantity: "Количество",
  price: "Цена",
  discount: "Скидка",
  cost: "Себестоимость",
  avg_check: "Средний чек",
  value: "Значение",
  count: "Количество",
  total: "Итого",
  amount: "Сумма",
  total_sold: "Продано",
  total_sales: "Продажи",
  sales: "Продажи",
  region_decline: "Спад в регионе",
  weak_manager: "Слабый менеджер",
  anomaly: "Аномалия",
  category: "Категория",
  manager: "Менеджер",
  product: "Товар",
  product_name: "Товар",
  month: "Месяц",
  date: "Дата",
  point_of_sale: "Филиал",
  store: "Магазин",
  manufacturer: "Производитель",
};

// Columns that should never be used as chart metrics
const ID_COLUMNS = new Set([
  "product_id", "category_id", "manufacturer_id", "customer_id",
  "order_id", "store_id", "purchase_id", "delivery_id", "id",
  "sku_code", "barcode", "article", "code", "sku",
]);

// Columns that are preferred for X-axis labels
const LABEL_PREFERRED = [
  "product_name", "product", "category", "manager", "region",
  "point_of_sale", "store", "manufacturer", "name",
];

import { formatDateRu } from "../utils/formatDate";

const translateKey = (key: any): string => {
  if (!key) return "";
  // Check if it's a date value
  const formatted = formatDateRu(key);
  if (formatted) return formatted;
  const lower = String(key).toLowerCase();
  if (KEY_TRANSLATIONS[lower]) return KEY_TRANSLATIONS[lower];
  // Fallback: replace underscores with spaces and capitalize
  return String(key)
    .replace(/_/g, " ")
    .replace(/\b\w/g, (l) => l.toUpperCase());
};

const formatValue = (value: any, name: any): [string, string] => {
  const nameStr = String(name);
  const formatted = new Intl.NumberFormat("ru-RU").format(value);
  return [formatted, translateKey(nameStr)];
};

const formatYAxis = (value: number) => {
  const absVal = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (absVal >= 1000000) return `${sign}${+(absVal / 1000000).toFixed(1)} млн`;
  if (absVal >= 1000) return `${sign}${+(absVal / 1000).toFixed(1)} тыс`;
  return String(value);
};

const generateTicks = (min: number, max: number, count: number = 5) => {
  const range = Math.max(max, 0) - Math.min(min, 0);
  if (range <= 0) return [0, 25, 50, 75, 100];
  const step = range / (count - 1);
  const magnitude = Math.pow(10, Math.floor(Math.log10(step)));
  const normalizedStep = step / magnitude;
  
  let niceStep;
  if (normalizedStep <= 1) niceStep = 1;
  else if (normalizedStep <= 2) niceStep = 2;
  else if (normalizedStep <= 5) niceStep = 5;
  else niceStep = 10;

  const finalStep = niceStep * magnitude;
  const ticks = [0];
  
  let currentTick = 0;
  while (currentTick + finalStep <= max) {
    currentTick += finalStep;
    ticks.push(currentTick);
  }
  if (currentTick < max) ticks.push(currentTick + finalStep);

  currentTick = 0;
  while (currentTick - finalStep >= min) {
    currentTick -= finalStep;
    ticks.unshift(currentTick);
  }
  // Add one more negative tick only if the minimum value is significantly below the last tick
  if (currentTick > min && (currentTick - min) > finalStep * 0.1) {
    ticks.unshift(currentTick - finalStep);
  }

  return ticks;
};

export default memo(function ChatMessage({
  message,
  onPlayAudio,
  onStopAudio,
  onPauseAudio,
  audioState,
  showCharts = true,
  onFeedback,
  onRetry,
}: ChatMessageProps) {
  const isUser = message.role === "user";
  const [sqlExpanded, setSqlExpanded] = useState(false);

  const renderChart = () => {
    if (!showCharts) return null;
    if (!message.data || message.data.length === 0 || !message.chartType)
      return null;

    // For single dict data wrapped in array
    if (message.data.length === 1 && Object.keys(message.data[0]).length > 2) {
      return null; // Better rendered as text/table
    }

    const { chartType, data } = message;

    if (chartType === "line") {
      // Prefer human-readable label columns for X-axis
      const categoryKey =
        Object.keys(data[0]).find((k) => LABEL_PREFERRED.includes(k)) ||
        Object.keys(data[0]).find((k) => typeof data[0][k] === "string") ||
        Object.keys(data[0])[0];
      const keys = Object.keys(data[0]).filter(
        (k) => k !== categoryKey && typeof data[0][k] === "number" && !ID_COLUMNS.has(k),
      );
      if (keys.length === 0) return null;
      const flatData = data.flatMap((d: any) =>
        keys.map((k) => Number(d[k]) || 0),
      );
      const minY = Math.min(...flatData);
      const maxY = Math.max(...flatData);

      const customTicks = generateTicks(minY, maxY);
      const domainMin = minY < 0 ? minY * 1.1 : 0;
      const domainMax = customTicks[customTicks.length - 1];

      return (
        <div className="chat-message__chart">
          <ResponsiveContainer width="100%" height={250} minWidth={0}>
            <LineChart data={data} margin={{ top: 25, right: 10, left: 10, bottom: 5 }}>
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="var(--border-light)"
              />
              <XAxis
                dataKey={categoryKey}
                stroke="#94a3b8"
                tickFormatter={translateKey}
              />
              <YAxis
                stroke="#94a3b8"
                width={110}
                tickFormatter={formatYAxis}
                domain={[domainMin, domainMax]}
                allowDataOverflow={true}
              />
              <Tooltip formatter={formatValue} labelFormatter={translateKey} />
              <Legend />
              {keys.map((key, i) => (
                <Line
                  key={key}
                  type="monotone"
                  dataKey={key}
                  name={translateKey(key)}
                  stroke={COLORS[i % COLORS.length]}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      );
    }

    if (chartType === "bar") {
      // Use chartLimit from question if available, otherwise limit to 20
      const maxItems = message.chartLimit || 20;
      const limitedData = data.length > maxItems ? data.slice(0, maxItems) : data;
      // Prefer human-readable label columns for X-axis
      const categoryKey =
        Object.keys(limitedData[0]).find((k) => LABEL_PREFERRED.includes(k)) ||
        Object.keys(limitedData[0]).find((k) => typeof limitedData[0][k] === "string") ||
        Object.keys(limitedData[0])[0];
      const keys = Object.keys(limitedData[0]).filter(
        (k) => k !== categoryKey && typeof limitedData[0][k] === "number" && !ID_COLUMNS.has(k),
      );
      if (keys.length === 0) return null;
      const flatData = limitedData.flatMap((d: any) =>
        keys.map((k) => Number(d[k]) || 0),
      );
      const minY = Math.min(...flatData);
      const maxY = Math.max(...flatData);

      const customTicks = generateTicks(minY, maxY);
      const domainMin = minY < 0 ? minY * 1.1 : 0;
      const domainMax = customTicks[customTicks.length - 1];

      return (
        <div className="chat-message__chart">
          <ResponsiveContainer width="100%" height={250} minWidth={0}>
            <BarChart data={limitedData} margin={{ top: 25, right: 10, left: 10, bottom: 5 }}>
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="var(--border-light)"
              />
              <XAxis
                dataKey={categoryKey}
                stroke="#94a3b8"
                tickFormatter={translateKey}
              />
              <YAxis
                stroke="#94a3b8"
                width={110}
                tickFormatter={formatYAxis}
                domain={[domainMin, domainMax]}
                ticks={customTicks}
              />
              <Tooltip cursor={false} formatter={formatValue} labelFormatter={translateKey} />
              <Legend />
              {keys.map((key, i) => {
                const isAllNegative = limitedData.length > 0 && limitedData.every((entry: any) => Number(entry[key]) < 0);
                const baseColor = isAllNegative ? "#ef4444" : COLORS[i % COLORS.length];
                
                return (
                  <Bar
                    key={key}
                    dataKey={key}
                    name={translateKey(key)}
                    fill={baseColor}
                  >
                    {limitedData.map((entry: any, index: number) => (
                      <Cell
                        key={`cell-${index}`}
                        fill={Number(entry[key]) < 0 ? "#ef4444" : baseColor}
                      />
                    ))}
                  </Bar>
                );
              })}
            </BarChart>
          </ResponsiveContainer>
        </div>
      );
    }

    if (chartType === "pie") {
      // Prefer human-readable label columns for X-axis
      const categoryKey =
        Object.keys(data[0]).find((k) => LABEL_PREFERRED.includes(k)) ||
        Object.keys(data[0]).find((k) => typeof data[0][k] === "string") ||
        Object.keys(data[0])[0];

      // Use LLM-specified column if available, otherwise auto-detect
      const numKeys = Object.keys(data[0]).filter(
        (k) => k !== categoryKey && typeof data[0][k] === "number" && !ID_COLUMNS.has(k),
      );
      // Prefer exact or suffix matches for revenue columns
      const isRevenueCol = (k: string) => {
        const lk = k.toLowerCase();
        return lk === "revenue" || lk === "sales_amount" || lk === "total_sales_amount"
          || lk === "total_revenue" || lk.endsWith("_revenue") || lk.endsWith("_amount");
      };
      const isCountCol = (k: string) => {
        const lk = k.toLowerCase();
        return lk === "sales_count" || lk === "count" || lk === "order_count"
          || lk.endsWith("_count") || lk.includes("quantity");
      };
      const valueKey =
        (message.chartValueKey && numKeys.includes(message.chartValueKey)) ? message.chartValueKey :
        numKeys.find((k) => isRevenueCol(k)) ||
        numKeys.find((k) => !isCountCol(k)) ||
        numKeys[0] ||
        null;
      if (!valueKey) return null;
      return (
        <div className="chat-message__chart">
          <ResponsiveContainer width="100%" height={250} minWidth={0}>
            <PieChart>
              <Pie
                data={data}
                dataKey={valueKey}
                nameKey={categoryKey}
                cx="50%"
                cy="50%"
                outerRadius={80}
              >
                {data.map((_, index) => (
                  <Cell
                    key={`cell-${index}`}
                    fill={COLORS[index % COLORS.length]}
                  />
                ))}
              </Pie>
              <Tooltip 
                formatter={(value: any, name: any) => {
                  const totalPieValue = data.reduce((sum: number, item: any) => sum + Number(item[valueKey] || 0), 0);
                  const num = Number(value);
                  const percent = totalPieValue > 0 ? ((num / totalPieValue) * 100).toFixed(1) : 0;
                  const formattedValStr = formatValue(num, name)[0];
                  return [`${formattedValStr} (${percent}%)`, translateKey(name as string)];
                }}
              />
              <Legend 
                layout="vertical" verticalAlign="middle" align="right"
                formatter={(value) => {
                  const item = data.find((c: any) => c[categoryKey] === value);
                  if (item) {
                    const totalPieValue = data.reduce((sum: number, i: any) => sum + Number(i[valueKey] || 0), 0);
                    const num = Number(item[valueKey]);
                    const percent = totalPieValue > 0 ? ((num / totalPieValue) * 100).toFixed(1) : 0;
                    return `${translateKey(value)} (${percent}%)`;
                  }
                  return translateKey(value);
                }}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
      );
    }

    if (chartType === "scatter") {
      const keys = Object.keys(data[0]);
      const numKeys = keys.filter((k) => typeof data[0][k] === "number" && !ID_COLUMNS.has(k));
      if (numKeys.length < 2) return null;
      const xKey = numKeys[0];
      const yKey = numKeys[1];
      return (
        <div className="chat-message__chart">
          <ResponsiveContainer width="100%" height={300} minWidth={0}>
            <ScatterChart>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey={xKey} name={translateKey(xKey)} tickFormatter={formatYAxis} />
              <YAxis dataKey={yKey} name={translateKey(yKey)} tickFormatter={formatYAxis} />
              <Tooltip
                formatter={(value: any, name: any) => formatValue(value, name)}
                labelFormatter={(label: any) => String(label)}
              />
              <Scatter data={data} fill={COLORS[0]} />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      );
    }

    if (chartType === "heatmap") {
      const keys = Object.keys(data[0]);
      const strKeys = keys.filter((k) => typeof data[0][k] === "string");
      const numKeys = keys.filter((k) => typeof data[0][k] === "number" && !ID_COLUMNS.has(k));
      if (strKeys.length < 2 || numKeys.length < 1) return null;
      const rowKey = strKeys[0];
      const colKey = strKeys[1];
      const valKey = numKeys[0];

      const rowLabels = [...new Set(data.map((d: any) => d[rowKey]))];
      const colLabels = [...new Set(data.map((d: any) => d[colKey]))];
      const values = data.map((d: any) => Number(d[valKey]) || 0);
      const maxVal = Math.max(...values, 1);

      const getColor = (v: number) => {
        const intensity = Math.round((v / maxVal) * 200);
        return `rgb(${255 - intensity}, ${255 - intensity}, 255)`;
      };

      return (
        <div className="chat-message__chart">
          <div className="heatmap-container">
            <div className="heatmap-header">
              <div className="heatmap-corner" />
              {colLabels.map((c) => (
                <div key={c} className="heatmap-label">{String(c).slice(0, 12)}</div>
              ))}
            </div>
            {rowLabels.map((r) => (
              <div key={r} className="heatmap-row">
                <div className="heatmap-label">{String(r).slice(0, 15)}</div>
                {colLabels.map((c) => {
                  const item = data.find((d: any) => d[rowKey] === r && d[colKey] === c);
                  const val = item ? Number(item[valKey]) || 0 : 0;
                  return (
                    <div
                      key={`${r}-${c}`}
                      className="heatmap-cell"
                      style={{ background: getColor(val) }}
                      title={`${r} × ${c}: ${val.toLocaleString("ru-RU")}`}
                    >
                      {val > 0 ? val.toLocaleString("ru-RU", { maximumFractionDigits: 0 }) : ""}
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
  };

  const renderDataTable = () => {
    if (!message.data || message.data.length === 0) return null;
    // Don't render table if chart was rendered
    if (renderChart()) return null;
    // Don't render for single-row KPI-like data
    if (message.data.length === 1 && Object.keys(message.data[0]).length > 3) return null;

    const columns = Object.keys(message.data[0]);

    return (
      <div className="chat-message__table-wrapper">
        <table className="chat-message__table">
          <thead>
            <tr>
              <th className="chat-message__table-num">#</th>
              {columns.map((col) => (
                <th key={col}>{translateKey(col)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {message.data.map((row, i) => (
              <tr key={i}>
                <td className="chat-message__table-num">{i + 1}</td>
                {columns.map((col) => (
                  <td key={col}>
                    {typeof row[col] === 'number'
                      ? formatValue(row[col], col)[0]
                      : row[col] == null ? (col.toLowerCase().includes('revenue') || col.toLowerCase().includes('sum') || col.toLowerCase().includes('total') || col.toLowerCase().includes('count') ? '0' : '(пусто)') : String(row[col])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  };

  return (
    <div
      className={`chat-message chat-message--${isUser ? "user" : "assistant"}`}
    >
      <div className="chat-message__avatar">
        {isUser ? <User size={20} /> : <Bot size={20} />}
      </div>
      <div className="chat-message__content">
        <div className="chat-message__text">
          {message.responseMode === "voice" ? (
            <span className="chat-message__voice-notice">
              [Ответ передан голосом]
            </span>
          ) : (
            cleanText(message.text)
          )}
          {!isUser && message.id !== "1" && (
            <div className="chat-message__audio-controls">
              {message.retryable && onRetry ? (
                <button
                  className="btn btn--tertiary btn--icon btn-play-audio"
                  onClick={onRetry}
                  data-tooltip-id="msg-tooltip" data-tooltip-content="Повторить запрос" data-tooltip-place="top"
                >
                  <RefreshCw size={28} />
                </button>
              ) : (
                <>
                  {audioState?.text === message.text && audioState?.status === 'playing' ? (
                    onPauseAudio && (
                      <button
                        className="btn btn--tertiary btn--icon btn-play-audio"
                        onClick={onPauseAudio}
                        data-tooltip-id="msg-tooltip" data-tooltip-content="Пауза" data-tooltip-place="top"
                      >
                        <PauseCircle size={28} />
                      </button>
                    )
                  ) : (
                    <button
                      className="btn btn--tertiary btn--icon btn-play-audio"
                      onClick={() => onPlayAudio(message.text)}
                      data-tooltip-id="msg-tooltip" data-tooltip-content="Прослушать" data-tooltip-place="top"
                    >
                      <PlayCircle size={28} />
                    </button>
                  )}
                  {onStopAudio && (
                    <button
                      className="btn btn--tertiary btn--icon btn-play-audio"
                      onClick={onStopAudio}
                      data-tooltip-id="msg-tooltip" data-tooltip-content="Остановить" data-tooltip-place="top"
                    >
                      <StopCircle size={28} />
                    </button>
                  )}
                </>
              )}
            </div>
          )}
        </div>



        {renderChart()}
        {renderDataTable()}

        {!isUser && message.sql && (
          <div className="chat-message__sql-block">
            <button
              className="chat-message__sql-toggle"
              onClick={() => setSqlExpanded(!sqlExpanded)}
            >
              {sqlExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              <span>Детали запроса</span>
            </button>
            {sqlExpanded && (
              <pre className="chat-message__sql-code">{message.sql}</pre>
            )}
          </div>
        )}

        {!isUser && onFeedback && message.id !== "1" && (
          <div className="chat-message__feedback">
            <button
              className={`chat-message__feedback-btn ${message.feedback === "positive" ? "chat-message__feedback-btn--active" : ""}`}
              onClick={() => onFeedback(message.id, message.feedback === "positive" ? null : "positive")}
              data-tooltip-id="msg-tooltip" data-tooltip-content="Ответ полезен" data-tooltip-place="top"
            >
              <ThumbsUp size={14} />
            </button>
            <button
              className={`chat-message__feedback-btn chat-message__feedback-btn--negative ${message.feedback === "negative" ? "chat-message__feedback-btn--active-negative" : ""}`}
              onClick={() => onFeedback(message.id, message.feedback === "negative" ? null : "negative")}
              data-tooltip-id="msg-tooltip" data-tooltip-content="Ответ не полезен" data-tooltip-place="top"
            >
              <ThumbsDown size={14} />
            </button>
          </div>
        )}

        {message.processingTime && (
          <div className="chat-message__meta">
            Время обработки: {message.processingTime.toFixed(2)} сек.
            {message.isCached && message.cachedAt && (
              <span className="chat-message__cache-badge" style={{ marginLeft: "8px", color: "var(--accent)" }}>
                (кэш от {new Date(message.cachedAt).toLocaleString("ru-RU")})
              </span>
            )}
          </div>
        )}
      </div>
      <ReactTooltip id="msg-tooltip" delayShow={300} />
    </div>
  );
});
