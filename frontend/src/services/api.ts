/**
 * @deprecated
 * This file is a backward-compatible re-export barrel.
 *
 * New code should import directly from the domain modules:
 *   import { dataApi }      from '@/shared/api/data.api'
 *   import { analyticsApi } from '@/shared/api/analytics.api'
 *   import { aiApi }        from '@/shared/api/ai.api'
 *   import { sessionsApi }  from '@/shared/api/sessions.api'
 *   import { voiceApi }     from '@/shared/api/voice.api'
 *   import { settingsApi }  from '@/shared/api/settings.api'
 *   import { usersApi }     from '@/shared/api/users.api'
 */

// Re-export the singleton client (named `api` for legacy callers)
export { apiClient as api } from '../shared/api/client';

// Re-export all domain APIs and types
export * from '../shared/api/data.api';
export * from '../shared/api/analytics.api';
export * from '../shared/api/ai.api';
export * from '../shared/api/sessions.api';
export * from '../shared/api/voice.api';
export * from '../shared/api/settings.api';
export * from '../shared/api/users.api';
export * from '../shared/types';
export * from '../shared/api/auth.api';
