import apiClient from './client';

export interface KpiData {
  total_revenue: number;
  total_profit: number;
  order_count: number;
  avg_check: number;
  avg_discount: number;
  total_quantity: number;
  revenue_change: number | null;
  profit_change: number | null;
  orders_change: number | null;
}

export interface CategoryData {
  category: string;
  revenue: number;
  profit: number;
  orders: number;
  quantity: number;
  share: number | null;
}

export interface TimelineData {
  period: string;
  revenue: number;
  profit: number;
  orders: number;
  quantity: number;
}

export const analyticsApi = {
  getKpis:        async (params?: Record<string, unknown>): Promise<KpiData>        => (await apiClient.get('/analytics/kpis', { params })).data,
  getCategories:  async (params?: Record<string, unknown>): Promise<CategoryData[]> => (await apiClient.get('/analytics/categories', { params })).data,
  getTimeline:    async (params?: Record<string, unknown>): Promise<TimelineData[]> => (await apiClient.get('/analytics/timeline', { params })).data,
  getTopProducts: async (params?: Record<string, unknown>)                          => (await apiClient.get('/analytics/top-products', { params })).data,
};
