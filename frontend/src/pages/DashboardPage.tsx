import { useEffect, useState } from "react";
import { formatDateRu } from "../utils/formatDate";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import { analyticsApi, dataApi } from "../services/api";
import type {
  KpiData,
  CategoryData,
  TimelineData,
  DatasetGroup,
} from "../services/api";
import { useDataset } from "../context/DatasetContext";
import KpiCard from "../components/KpiCard";
import ChartContainer from "../components/ChartContainer";
import { Database } from "lucide-react";
import "./DashboardPage.css";

const COLORS = [
  "#6366f1",
  "#ec4899",
  "#14b8a6",
  "#f59e0b",
  "#8b5cf6",
  "#f43f5e",
  "#06b6d4",
  "#10b981",
  "#d946ef",
  "#f97316",
  "#0ea5e9",
  "#84cc16",
  "#3b82f6",
  "#eab308",
  "#ef4444",
  "#64748b",
];

export default function DashboardPage() {
  const [kpis, setKpis] = useState<KpiData | null>(null);
  const [timeline, setTimeline] = useState<TimelineData[]>([]);
  const [categories, setCategories] = useState<CategoryData[]>([]);
  const [topProducts, setTopProducts] = useState<any[]>([]);
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [groups, setGroups] = useState<DatasetGroup[]>([]);
  const { selectedDatasetId, setSelectedDatasetId, setSelectedGroupId } = useDataset();

  // Determine if current selection is a group
  const activeGroup = groups.find(g => g.tables.length > 1 && g.tables.some(t => t.id === selectedDatasetId));
  const isGroupMode = !!activeGroup;

  const loadDatasets = async () => {
    try {
      const data = await dataApi.getGroups();
      setGroups(data);
      if (!selectedDatasetId && data.length > 0) {
        const firstGroup = data.find(g => g.tables.length > 1);
        if (firstGroup) {
          setSelectedGroupId(firstGroup.group_id);
          setSelectedDatasetId(firstGroup.tables[0].id);
        } else {
          setSelectedDatasetId(data[0].tables[0].id);
        }
      }
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    loadDatasets();
  }, []);

  useEffect(() => {
    if (isGroupMode) {
      setKpis(null);
      setTimeline([]);
      setCategories([]);
      setTopProducts([]);
      setLoading(false);
    } else if (selectedDatasetId) {
      setLoading(true);
      const params = { dataset_id: selectedDatasetId };
      Promise.all([
        analyticsApi.getKpis(params),
        analyticsApi.getTimeline(params),
        analyticsApi.getCategories(params),
      ]).then(([kpiData, timeData, catData]) => {
        setKpis(kpiData);
        setTimeline(timeData);
        setCategories(catData);
      }).catch(err => {
        console.error("Error fetching dashboard data", err);
      }).finally(() => {
        setLoading(false);
      });

      analyticsApi.getTopProducts(params).then(data => {
        setTopProducts(data);
      }).catch(err => {
        console.error("Error fetching top products", err);
      });
    }
  }, [selectedDatasetId, isGroupMode]);

  // Fetch top products when category selection changes
  useEffect(() => {
    if (!selectedDatasetId || isGroupMode) return;
    const params: any = { dataset_id: selectedDatasetId, limit: 7 };
    if (selectedCategory) {
      params.category = selectedCategory;
    }
    analyticsApi.getTopProducts(params).then(data => {
      setTopProducts(data);
    }).catch(err => {
      console.error("Error fetching top products for category", err);
    });
  }, [selectedCategory, selectedDatasetId, isGroupMode]);

  const formatCurrency = (value: number) => {
    if (value == null) return "0";
    const absVal = Math.abs(value);
    const sign = value < 0 ? "-" : "";
    if (absVal >= 1000000000)
      return `${sign}${(absVal / 1000000000).toFixed(1)} млрд`;
    if (absVal >= 1000000)
      return `${sign}${(absVal / 1000000).toFixed(1)} млн`;
    if (absVal >= 1000) return `${sign}${(absVal / 1000).toFixed(1)} тыс.`;
    return `${sign}${absVal}`;
  };

  const formatExactCurrency = (value: number) => {
    if (value == null) return "";
    return new Intl.NumberFormat("ru-RU").format(value);
  };

  return (
    <div className="scrollable-page">
      <div className="dashboard-page page-container">
        <header
          className="page-header animate-fade-in"
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div>
            <h1>Обзор продаж</h1>
            <p className="text-muted">Аналитика ключевых показателей</p>
          </div>

          <div
            className="dataset-selector"
            style={{ display: "flex", alignItems: "center", gap: "10px" }}
          >
            <span
              style={{ color: "var(--text-secondary)", fontSize: "0.9rem" }}
            >
              База данных:
            </span>
            <select
              value={activeGroup ? activeGroup.group_id : (selectedDatasetId || "")}
              onChange={(e) => {
                const val = e.target.value;
                const group = groups.find(g => g.group_id === val);
                if (group) {
                  setSelectedGroupId(group.group_id);
                  setSelectedDatasetId(group.tables[0].id);
                } else {
                  setSelectedGroupId(null);
                  setSelectedDatasetId(val || null);
                }
              }}
              className="select-input"
              style={{
                padding: "8px 12px",
                borderRadius: "8px",
                background: "var(--bg-card)",
                border: "1px solid var(--border-light)",
                color: "var(--text-primary)",
                cursor: "pointer",
                outline: "none",
              }}
            >
              {groups.map((g) =>
                g.tables.length === 1 ? (
                  <option key={g.tables[0].id} value={g.tables[0].id}>
                    {g.tables[0].name}
                  </option>
                ) : (
                  <option key={g.group_id} value={g.group_id}>
                    {g.source_file}
                  </option>
                )
              )}
            </select>
          </div>
        </header>

        {isGroupMode ? (
          <div className="card" style={{ padding: '40px', textAlign: 'center', marginTop: '20px' }}>
            <Database size={48} style={{ color: 'var(--accent)', marginBottom: '16px' }} />
            <h3 style={{ marginBottom: '8px' }}>Выбрана группа баз данных</h3>
            <p className="text-muted">
              Отображение дашборда невозможно для группы связанных таблиц.
              <br />
              Используйте ассистент для анализа данных из нескольких таблиц.
            </p>
          </div>
        ) : (
        <>
        <div className="kpi-grid">
          <KpiCard
            title="Выручка"
            value={kpis ? formatCurrency(kpis.total_revenue) : "0"}
            subtitle={
              kpis ? formatExactCurrency(kpis.total_revenue) : undefined
            }
            change={kpis?.revenue_change}
          />
          <KpiCard
            title="Прибыль"
            value={kpis ? formatCurrency(kpis.total_profit) : "0"}
            subtitle={
              kpis ? formatExactCurrency(kpis.total_profit) : undefined
            }
            change={kpis?.profit_change}
          />
          <KpiCard
            title="Заказы"
            value={kpis ? String(kpis.order_count) : "0"}
            change={kpis?.orders_change}
          />
          <KpiCard
            title="Средний чек"
            value={kpis ? formatCurrency(kpis.avg_check) : "0"}
            subtitle={
              kpis ? formatExactCurrency(kpis.avg_check) : undefined
            }
          />
        </div>

        <div className="charts-grid-main">
          <ChartContainer title="Динамика выручки" loading={loading}>
            <ResponsiveContainer width="100%" height="100%" minWidth={0}>
              <LineChart
                data={timeline}
                margin={{ top: 20, right: 30, left: 10, bottom: 30 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="var(--border-light)"
                  vertical={false}
                />
                <XAxis
                  dataKey="period"
                  stroke="var(--text-muted)"
                  tickMargin={12}
                  tickFormatter={(val) => formatDateRu(val) || val}
                />
                <YAxis
                  stroke="var(--text-muted)"
                  width={110}
                  tickFormatter={formatCurrency}
                />
                <Tooltip
                  formatter={(val: any) => formatCurrency(val)}
                  labelFormatter={(label) => formatDateRu(label) || label}
                />
                <Legend
                  verticalAlign="bottom"
                  height={36}
                  wrapperStyle={{ bottom: "10px" }}
                />
                <Line
                  type="monotone"
                  dataKey="revenue"
                  name="Выручка"
                  stroke="var(--primary)"
                  strokeWidth={3}
                  dot={{ r: 4 }}
                  activeDot={{ r: 6 }}
                />
                <Line
                  type="monotone"
                  dataKey="profit"
                  name="Прибыль"
                  stroke="var(--accent)"
                  strokeWidth={3}
                  dot={{ r: 4 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </ChartContainer>

          <div className="charts-grid-sub">
            <ChartContainer title="Продажи по категориям" loading={loading} className="chart-container--pie">
              <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                <PieChart>
                  <Pie
                    data={categories}
                    dataKey="revenue"
                    nameKey="category"
                    cx="50%"
                    cy="50%"
                    innerRadius={60}
                    outerRadius={80}
                    paddingAngle={5}
                    stroke="none"
                  >
                    {categories.map((entry, index) => {
                      const isSelected = selectedCategory === entry.category;
                      return (
                        <Cell
                          key={`cell-${index}`}
                          fill={COLORS[index % COLORS.length]}
                          stroke="none"
                          opacity={selectedCategory && !isSelected ? 0.1 : 1}
                          onClick={() =>
                            setSelectedCategory(
                              isSelected ? null : entry.category,
                            )
                          }
                          style={{
                            cursor: "pointer",
                            transition: "opacity 0.2s",
                          }}
                        />
                      );
                    })}
                  </Pie>
                  <Tooltip
                    formatter={(value: any, name: any) => {
                      const totalCategoryRevenue = categories.reduce(
                        (sum, item) => sum + Number(item.revenue),
                        0,
                      );
                      const num = Number(value);
                      const percent =
                        totalCategoryRevenue > 0
                          ? ((num / totalCategoryRevenue) * 100).toFixed(1)
                          : 0;
                      return [`${formatCurrency(num)} (${percent}%)`, name];
                    }}
                  />
                  <Legend
                    layout="vertical"
                    verticalAlign="middle"
                    align="right"
                    wrapperStyle={{ top: "50%", transform: "translateY(-50%)" }}
                    onClick={(data) => {
                      const category = data.value;
                      setSelectedCategory((prev) =>
                        prev === category ? null : (category ?? null),
                      );
                    }}
                    formatter={(value) => {
                      const item = categories.find((c) => c.category === value);
                      if (item) {
                        const totalCategoryRevenue = categories.reduce(
                          (sum, i) => sum + Number(i.revenue),
                          0,
                        );
                        const num = Number(item.revenue);
                        const percent =
                          totalCategoryRevenue > 0
                            ? ((num / totalCategoryRevenue) * 100).toFixed(1)
                            : 0;
                        return `${value} (${percent}%)`;
                      }
                      return value;
                    }}
                  />
                </PieChart>
              </ResponsiveContainer>
              <p style={{ position: 'absolute', bottom: 0, left: 0, right: 0, textAlign: 'center', color: 'var(--text-muted)', fontSize: '0.75rem', margin: 0, opacity: 0.7, pointerEvents: 'none' }}>
                Выберите сегмент диаграммы, чтобы увидеть топ товаров категории
              </p>
            </ChartContainer>

            <ChartContainer
              title={
                selectedCategory
                  ? `Топ товаров из категории ${selectedCategory}`
                  : "Топ товаров"
              }
              loading={loading}
            >
              <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                <BarChart
                  data={topProducts}
                  layout="vertical"
                  margin={{ top: 5, right: 30, left: 10, bottom: 5 }}
                >
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="var(--border-light)"
                    horizontal={false}
                  />
                  <XAxis
                    type="number"
                    stroke="var(--text-muted)"
                    tickFormatter={formatCurrency}
                  />
                  <YAxis
                    dataKey="product"
                    type="category"
                    stroke="var(--text-muted)"
                    width={180}
                    tick={{ fontSize: 11 }}
                  />
                  <Tooltip
                    cursor={false}
                    formatter={(val: any) => formatCurrency(val)}
                  />
                  <Bar
                    dataKey="revenue"
                    name="Выручка"
                    fill={COLORS[0]}
                    radius={[0, 4, 4, 0]}
                    barSize={24}
                  />
                </BarChart>
              </ResponsiveContainer>
            </ChartContainer>
          </div>
        </div>
        </>
        )}
      </div>
    </div>
  );
}
