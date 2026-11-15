import apiClient from './client';

export const voiceApi = {
  transcribe: async (audioBlob: Blob, signal?: AbortSignal) => {
    const fd = new FormData();
    fd.append('file', audioBlob, 'audio.webm');
    return (await apiClient.post('/voice/transcribe', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      signal,
    })).data;
  },
  synthesizeUrl: () => '/api/v1/voice/synthesize',
  /** Адрес потокового синтеза для <audio>: тот же хост API, что у apiClient (в Electron страница открыта по file://) */
  synthesizeStreamUrl: (text: string, token: string | null) =>
    `${apiClient.defaults.baseURL}/voice/synthesize_stream?text=${encodeURIComponent(text)}${token ? `&token=${encodeURIComponent(token)}` : ''}`,
};

