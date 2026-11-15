/**
 * Подписи колонок ответа с бэкенда (app/domain/ai/data_format.py, column_labels) — единый источник:
 * новый ключ добавляется только там. Свой словарь в ChatMessage — запасной, пока ответ не пришёл.
 */
import { useSyncExternalStore } from 'react';
import { settingsApi } from '../api/settings.api';

let labels: Record<string, string> = {};
let version = 0;
let pending = false;
let loaded = false;
const listeners = new Set<() => void>();

function load() {
  if (pending || loaded) return;
  pending = true;
  settingsApi
    .getColumnLabels()
    .then((data) => {
      labels = data && typeof data === 'object' ? data : {};
      loaded = true;
      version += 1;
      listeners.forEach((l) => l());
    })
    // Не загрузились (сервер недоступен) — остаёмся на запасном словаре, повторим при следующем монтировании
    .catch(() => undefined)
    .finally(() => {
      pending = false;
    });
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  load();
  return () => listeners.delete(listener);
}

/** Подпись ключа с бэкенда или undefined */
export function serverColumnLabel(key: string): string | undefined {
  return labels[key.toLowerCase()];
}

/** Подписаться на подписи: компонент перерисуется, когда они загрузятся */
export function useColumnLabels(): number {
  return useSyncExternalStore(subscribe, () => version);
}

/** Словарь колонок изменён: подписи перечитать */
export function reloadColumnLabels() {
  loaded = false;
  load();
}
