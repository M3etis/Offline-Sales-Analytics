import apiClient from './client';

export type ModelFit = 'fast' | 'slow' | 'no';

export interface Hardware {
  os: string;
  arch: string;
  cpu?: string | null;
  cpu_cores?: number | null;
  ram_gb: number | null;
  /** apple — Apple Silicon (общая память), nvidia — видеокарта NVIDIA, null — только процессор */
  accelerator: 'apple' | 'nvidia' | null;
  gpu?: string | null;
  vram_gb?: number | null;
  disk_free_gb?: number | null;
}

export interface CatalogModel {
  name: string;
  title: string;
  params_b: number;
  size_gb: number;
  memory_gb: number;
  description: string;
  primary: boolean;
  min_ram_gb: number;
  installed: boolean;
  active: boolean;
  fit: ModelFit;
  fit_reason: string;
  disk_ok: boolean;
  recommended: boolean;
}

export interface OtherInstalledModel {
  name: string;
  size_gb: number;
  params?: string | null;
  active: boolean;
}

export interface PullState {
  status: 'idle' | 'running' | 'success' | 'error' | 'cancelled';
  model: string | null;
  phase?: 'manifest' | 'downloading' | 'verifying' | 'writing' | 'activating' | 'done';
  activate?: boolean;
  activated?: boolean;
  completed?: number;
  total?: number;
  speed_bps?: number | null;
  eta_s?: number | null;
  error?: string | null;
}

export interface ModelsOverview {
  hardware: Hardware;
  budgets: { fast_gb: number; total_gb: number };
  recommended: string | null;
  active_model: string;
  ollama: { status: 'ok' | 'offline' | 'error'; error?: string };
  models: CatalogModel[];
  other_installed: OtherInstalledModel[];
  pull: PullState;
}

export const modelsApi = {
  overview: async (): Promise<ModelsOverview> => (await apiClient.get('/ai/models')).data,
  /** Опрос раз в секунду, без действия пользователя: 401 не разлогинивает (см. skipAuthRedirect) */
  pullStatus: async (): Promise<PullState> => (await apiClient.get('/ai/models/pull', { skipAuthRedirect: true })).data,
  pull: async (name: string, activate = true): Promise<PullState> => (await apiClient.post('/ai/models/pull', { name, activate })).data,
  cancelPull: async (): Promise<PullState> => (await apiClient.delete('/ai/models/pull')).data,
  activate: async (name: string): Promise<{ active_model: string }> => (await apiClient.post('/ai/models/active', { name })).data,
};
