import apiClient from './client';
import type { UserOut, UserCreate, UserUpdate } from '../types';

export const usersApi = {
  getUsers:       async (): Promise<UserOut[]>                      => (await apiClient.get('/users')).data,
  createUser:     async (data: UserCreate): Promise<UserOut>        => (await apiClient.post('/users', data)).data,
  updateUser:     async (id: string, data: UserUpdate): Promise<UserOut> => (await apiClient.put(`/users/${id}`, data)).data,
  updatePassword: async (id: string, password: string)              => (await apiClient.put(`/users/${id}/password`, { password })).data,
  deleteUser:     async (id: string)                                => (await apiClient.delete(`/users/${id}`)).data,
};
