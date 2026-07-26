import apiClient from './client';
import type { Dataset, DatasetGroup, TableResponse } from '../types';

export interface ColumnPreview {
  key: string;
  type: string;
  sample?: string;
  nulls: number;
  unique: number;
}

export interface TablePreview {
  name: string;
  rows: number;
  columns: ColumnPreview[];
  sample: Record<string, unknown>[];
}

export interface DbPreview {
  filename: string;
  tables: TablePreview[];
  relationships: import('../types').Relationship[];
  total_rows: number;
  total_tables: number;
}

export interface UploadResponse {
  status: string;
  dataset_id: string;
  rows_loaded: number;
  total_rows: number;
  columns: string[];
  warnings: string[];
}

export const dataApi = {
  previewFile: async (file: File): Promise<DbPreview> => {
    const fd = new FormData();
    fd.append('file', file);
    const res = await apiClient.post('/data/preview', fd, { headers: { 'Content-Type': 'multipart/form-data' } });
    return res.data;
  },
  uploadFile: async (file: File, onProgress?: (p: number) => void): Promise<UploadResponse> => {
    const fd = new FormData();
    fd.append('file', file);
    const res = await apiClient.post('/data/upload', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (e) => {
        if (e.total && onProgress) onProgress(Math.round((e.loaded * 100) / e.total));
      },
    });
    return res.data;
  },
  loadDemo:        async (): Promise<UploadResponse> => (await apiClient.post('/data/demo')).data,
  loadDemoLuxury:  async (): Promise<UploadResponse> => (await apiClient.post('/data/demo-luxury')).data,
  uploadUrl:       async (url: string): Promise<UploadResponse> => (await apiClient.post('/data/url', { url })).data,
  getInfo:         async () => (await apiClient.get('/data/info')).data,
  getDatasets:     async (): Promise<Dataset[]> => (await apiClient.get('/data/datasets')).data,
  getGroups:       async (): Promise<DatasetGroup[]> => (await apiClient.get('/data/groups')).data,
  deleteDataset:   async (id: string) => (await apiClient.delete(`/data/datasets/${id}`)).data,
  deleteGroup:     async (groupId: string) => (await apiClient.delete(`/data/groups/${groupId}`)).data,
  renameDataset:   async (id: string, name: string) => (await apiClient.put(`/data/datasets/${id}/rename`, { name })).data,
  renameGroup:     async (groupId: string, name: string) => (await apiClient.put(`/data/groups/${groupId}/rename`, { name })).data,
  deleteAllDatasets: async () => (await apiClient.delete('/data/datasets')).data,
  getTable:        async (params: Record<string, unknown>): Promise<TableResponse> => (await apiClient.get('/data/table', { params })).data,
  fullCleanup:     async () => (await apiClient.delete('/data/full-cleanup')).data,
  getManifest:     async (datasetId: string) => (await apiClient.get(`/data/manifest/${datasetId}`)).data,
  rebuildDataset:  async (datasetId: string) => (await apiClient.post(`/data/rebuild/${datasetId}`)).data,
};
