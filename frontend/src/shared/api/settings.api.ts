import apiClient from './client';
import type { UserOut } from '../types';

export interface InstructionItem { id?: string; category: string; content: string; }
export interface InstructionsList { instructions: InstructionItem[]; }
export interface ColumnDictItem   { id?: string; en_name: string; ru_name: string; category: string; }

export const settingsApi = {
  getInstructions:    async (): Promise<InstructionsList>                  => (await apiClient.get('/settings/instructions')).data,
  saveInstructions:   async (data: InstructionsList)                       => (await apiClient.post('/settings/instructions', data)).data,
  getConfig:          async (): Promise<Record<string, string>>             => (await apiClient.get('/settings/config')).data,
  saveConfig:         async (key: string, value: string)                   => (await apiClient.post('/settings/config', { key, value })).data,
  clearSessions:      async (user_id?: string)                             => (await apiClient.delete(`/settings/clear-sessions${user_id ? '?user_id=' + encodeURIComponent(user_id) : ''}`)).data,
  clearCache:         async (user_id?: string)                             => (await apiClient.delete(`/settings/clear-cache${user_id ? '?user_id=' + encodeURIComponent(user_id) : ''}`)).data,
  getUsers:           async (): Promise<UserOut[]>                         => (await apiClient.get('/settings/users')).data,
  getColumnDictionary: async (category?: string, search?: string) => {
    const p = new URLSearchParams();
    if (category) p.append('category', category);
    if (search) p.append('search', search);
    return (await apiClient.get(`/settings/column-dictionary?${p}`)).data;
  },
  addColumnDictItem:      async (item: ColumnDictItem)       => (await apiClient.post('/settings/column-dictionary', item)).data,
  updateColumnDictItem:   async (id: string, item: ColumnDictItem) => (await apiClient.put(`/settings/column-dictionary/${id}`, item)).data,
  deleteColumnDictItem:   async (id: string)                 => (await apiClient.delete(`/settings/column-dictionary/${id}`)).data,
  restoreColumnDictDefaults: async ()                        => (await apiClient.post('/settings/column-dictionary/restore-defaults')).data,
  getColumnDictCategories:   async ()                        => (await apiClient.get('/settings/column-dictionary/categories')).data,
};
