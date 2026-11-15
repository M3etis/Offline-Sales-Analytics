import { useCallback } from 'react';
import { useAuth } from '../../shared/providers/AuthContext';
import { findNavItem, isNavItemAllowed } from './nav';

/**
 * Можно ли пользователю открыть раздел — по тем же правам, что меню и защита маршрутов (nav.ts).
 * Ссылки на разделы проверяют доступ через него, а не строкой права: иначе правила расходятся.
 */
export function useCanOpen(): (path: string) => boolean {
  const { isAdmin, hasPermission } = useAuth();
  return useCallback(
    (path: string) => {
      const item = findNavItem(path);
      return item ? isNavItemAllowed(item, isAdmin, hasPermission) : true;
    },
    [isAdmin, hasPermission],
  );
}
