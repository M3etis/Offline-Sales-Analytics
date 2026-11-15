import apiClient from './client';
import type { AskResponse } from '../types';

export interface ModelStatusResponse {
  status: 'ok' | 'offline' | 'error' | string;
  model?: string;
  model_available?: boolean;
  available_models?: string[];
  error?: string;
  hint?: string;
}

export const aiApi = {
  // Опрашивается в фоне — истёкший токен не должен разлогинивать без действия пользователя
  getModelStatus: async (): Promise<ModelStatusResponse> => (await apiClient.get('/ai/ollama-status', { skipAuthRedirect: true })).data,

  ask: async (
    question: string,
    extended = false,
    session_id?: string,
    dataset_id?: string | null
  ): Promise<AskResponse> => (await apiClient.post('/ai/ask', { question, extended, session_id, dataset_id })).data,

  askStream: async (
    question: string,
    extended = false,
    session_id?: string,
    dataset_id?: string | null,
    onStatus?: (message: string) => void
  ): Promise<AskResponse> => {
    const token = localStorage.getItem('token');
    const response = await fetch('/api/v1/ai/ask-stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ question, extended, session_id, dataset_id }),
    });

    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const reader = response.body?.getReader();
    if (!reader) throw new Error('No reader');

    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = JSON.parse(line.slice(6));
        if (data.status === 'progress' && onStatus) onStatus(data.message);
        else if (data.status === 'done') return data.result as AskResponse;
        else if (data.status === 'error') throw new Error(data.error || 'Unknown error');
      }
    }
    throw new Error('Stream ended without result');
  },
};
