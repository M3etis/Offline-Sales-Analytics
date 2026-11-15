/**
 * Domain API modules — each domain owns its own API surface.
 * 
 * Old: import { dataApi, analyticsApi, aiApi, sessionsApi } from '../services/api'
 * New: import { dataApi } from '@/shared/api'           (or from feature module)
 * 
 * Import domain APIs from here or directly from the *.api modules.
 */
export { apiClient, default } from './client';
export * from './data.api';
export * from './analytics.api';
export * from './ai.api';
export * from './models.api';
export * from './sessions.api';
export * from './voice.api';
export * from './settings.api';
export * from './users.api';
export * from './auth.api';
