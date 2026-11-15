import type { DatasetGroup } from '../types';
import { formatCount, ROWS, TABLES } from './format';

/**
 * Название базы — одно для переключателя, списка загрузок и шапок страниц:
 * многотабличная база называется по файлу, одиночная — по таблице (её и переименовывают).
 */
export function groupLabel(g: DatasetGroup): string {
  return g.tables.length > 1 ? g.source_file : g.tables[0]?.name || g.source_file;
}

/** Размер базы: число таблиц у многотабличной, число строк у одиночной */
export function groupMeta(g: DatasetGroup): string {
  return g.tables.length > 1 ? formatCount(g.tables.length, TABLES) : formatCount(g.tables[0]?.rows ?? 0, ROWS);
}

/**
 * Значение фильтра dataset_id для «Истории» и «Оценок ответов»: у многотабличной базы — group_id
 * (бэкенд раскрывает его в id всех таблиц), у одиночной — id таблицы.
 */
export function groupFilterId(g: DatasetGroup): string | null {
  return g.tables.length > 1 ? g.group_id : g.tables[0]?.id ?? null;
}

/**
 * Одна ли это база: таблицы одной многотабличной базы считаются одной базой — ассистент
 * всё равно работает со всей группой. Пока список баз не загружен, сравниваем id как есть.
 */
export function isSameBase(groups: DatasetGroup[], a: string | null, b: string | null): boolean {
  if (a === b) return true;
  const group = findGroupOf(groups, a);
  return group !== null && group.tables.some((t) => t.id === b);
}

/** База (группа любого размера), в которую входит выбранная таблица */
export function findGroupOf(groups: DatasetGroup[], datasetId: string | null): DatasetGroup | null {
  if (!datasetId) return null;
  return groups.find((g) => g.tables.some((t) => t.id === datasetId)) ?? null;
}
