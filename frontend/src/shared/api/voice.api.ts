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
};

