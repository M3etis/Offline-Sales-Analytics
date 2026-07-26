/**
 * Domain API modules — each domain owns its own API surface.
 * 
 * Old: import { dataApi, analyticsApi, aiApi, sessionsApi } from '../services/api'
 * New: import { dataApi } from '@/shared/api'           (or from feature module)
 * 
 * The original services/api.ts re-exports everything for backward compatibility.
 */
export { apiClient, default } from './client';
export * from './data.api';
export * from './analytics.api';
export * from './ai.api';
export * from './sessions.api';
export * from './voice.api';
export * from './settings.api';
export * from './users.api';
export * from './auth.api';
