import apiClient from './client';

export interface AuthResponse {
  access_token: string;
  token_type: string;
  role: string;
  username: string;
  full_name: string;
  permissions: string[];
}

export const authApi = {
  login: async (username: string, password: string): Promise<AuthResponse> => {
    const formData = new URLSearchParams();
    formData.append('username', username);
    formData.append('password', password);
    const res = await apiClient.post('/auth/login', formData, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    });
    return res.data;
  },
};
