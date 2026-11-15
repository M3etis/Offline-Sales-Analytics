import {
  Activity,
  BookOpen,
  History,
  LayoutDashboard,
  MessageSquare,
  SlidersHorizontal,
  Table2,
  ThumbsUp,
  Trash2,
  Upload,
  Users,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

export interface NavItem {
  path: string;
  label: string;
  icon: LucideIcon;
  permission?: string;
  adminOnly?: boolean;
}

export const PRODUCT_NAME = 'Offline Sales Analytics';

export const MAIN_NAV: NavItem[] = [
  { path: '/dashboard', label: 'Дашборд', icon: LayoutDashboard, permission: 'dashboard' },
  { path: '/analyst', label: 'Ассистент', icon: MessageSquare, permission: 'analyst' },
  { path: '/sessions', label: 'История', icon: History, permission: 'sessions' },
  { path: '/details', label: 'Данные', icon: Table2, permission: 'details' },
];

// Порядок по смыслу: данные → ассистент → администрирование; необратимая очистка — последней
export const SETTINGS_NAV: NavItem[] = [
  // Данные: загрузить файлы и подписать их колонки
  { path: '/settings/data', label: 'Загрузка данных', icon: Upload, permission: 'settings_data' },
  { path: '/settings/column-dictionary', label: 'Словарь колонок', icon: BookOpen, permission: 'settings_assistant' },
  // Ассистент: настроить ответы и посмотреть, как их оценивают
  { path: '/settings/assistant', label: 'Настройки ассистента', icon: SlidersHorizontal, permission: 'settings_assistant' },
  { path: '/settings/feedback', label: 'Оценки ответов', icon: ThumbsUp, permission: 'settings_assistant' },
  // Администрирование
  { path: '/settings/users', label: 'Пользователи', icon: Users, adminOnly: true },
  { path: '/settings/status', label: 'Статус служб', icon: Activity, permission: 'settings_status' },
  // Только администратор: все действия очистки на бэкенде требуют get_current_admin
  { path: '/settings/cleanup', label: 'Очистка', icon: Trash2, adminOnly: true },
];

export function findNavItem(pathname: string): NavItem | undefined {
  return [...MAIN_NAV, ...SETTINGS_NAV].find((item) => pathname === item.path || pathname.startsWith(`${item.path}/`));
}

/** Доступен ли раздел пользователю — одно правило для меню и для защиты маршрутов */
export function isNavItemAllowed(item: NavItem, isAdmin: boolean, hasPermission: (permission: string) => boolean): boolean {
  return item.adminOnly ? isAdmin : !item.permission || hasPermission(item.permission);
}

/** Первый доступный раздел: туда ведут вход, «/» и отказ в доступе; null — доступных разделов нет */
export function firstAllowedPath(isAdmin: boolean, hasPermission: (permission: string) => boolean): string | null {
  return [...MAIN_NAV, ...SETTINGS_NAV].find((item) => isNavItemAllowed(item, isAdmin, hasPermission))?.path ?? null;
}
