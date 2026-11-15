/**
 * Base Axios instance shared by all domain API modules.
 * A single interceptor handles auth token injection and 401 redirects.
 */
import axios from 'axios';
import { currentAppPath, hardNavigate } from '../lib/navigation';
import { backendUrl } from '../lib/backendUrl';

declare module 'axios' {
  interface AxiosRequestConfig {
    /**
     * Фоновый запрос (опрос статуса, тихое обновление списка): при 401 не выходить из учётной записи.
     * Иначе истёкший токен выбросит пользователя на вход посреди ввода без его действия —
     * выход произойдёт на следующем запросе, который пользователь сделает сам.
     */
    skipAuthRedirect?: boolean;
  }
}

const API_HOST = backendUrl();
const BASE_URL = API_HOST ? `${API_HOST}/api/v1` : '/api/v1';

export const apiClient = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
});

// ── Request: inject Bearer token ──────────────────────────────────────────────
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

/** Токен недействителен: забыть его и уйти на вход. Общая реакция для REST (401) и WebSocket (4001). */
export function handleUnauthorized(): void {
  localStorage.removeItem('token');
  localStorage.removeItem('role');
  if (currentAppPath() !== '/login') {
    hardNavigate('/login');
  }
}

// ── Response: handle 401 globally ────────────────────────────────────────────
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && !error.config?.skipAuthRedirect) {
      handleUnauthorized();
    }
    return Promise.reject(error);
  }
);

export default apiClient;
