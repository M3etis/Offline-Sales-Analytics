import apiClient from './client';
import type { ChatSession, SessionDetails, PaginatedSessions } from '../types';

export const sessionsApi = {
  getSessions: async (
    fromDate?: string, toDate?: string, dataset_id?: string | null,
    page?: number, page_size?: number
  ): Promise<PaginatedSessions> => {
    const p = new URLSearchParams();
    if (fromDate) p.append('from_date', fromDate);
    if (toDate) p.append('to_date', toDate);
    if (dataset_id) p.append('dataset_id', dataset_id);
    if (page) p.append('page', page.toString());
    if (page_size) p.append('page_size', page_size.toString());
    return (await apiClient.get(`/sessions${p.toString() ? '?' + p : ''}`)).data;
  },
  createSession:    async (title?: string, dataset_id?: string | null): Promise<ChatSession> => (await apiClient.post('/sessions', { title, dataset_id })).data,
  getSession:       async (id: string): Promise<SessionDetails>                               => (await apiClient.get(`/sessions/${id}`)).data,
  updateTitle:      async (id: string, title: string): Promise<ChatSession>                   => (await apiClient.put(`/sessions/${id}/title`, { title })).data,
  deleteSession:    async (id: string): Promise<void>                                         => { await apiClient.delete(`/sessions/${id}`); },
  clearMySessions:  async ()                                                                   => (await apiClient.delete('/sessions/clear-my')).data,
  setFeedback:      async (messageId: string, feedback: 'positive' | 'negative' | null)       => (await apiClient.post('/sessions/feedback', { message_id: messageId, feedback })).data,
  getFeedbackStats: async (datasetId?: string)                                                => (await apiClient.get(`/sessions/feedback-stats${datasetId ? '?dataset_id=' + datasetId : ''}`)).data,
  getFeedbackMessages: async (datasetId?: string, feedbackFilter?: string) => {
    const p = new URLSearchParams();
    if (datasetId) p.append('dataset_id', datasetId);
    if (feedbackFilter) p.append('feedback', feedbackFilter);
    return (await apiClient.get(`/sessions/feedback-messages${p.toString() ? '?' + p : ''}`)).data;
  },
};
