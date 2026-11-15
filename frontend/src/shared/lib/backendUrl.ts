/**
 * Адрес бэкенда. Сборка Electron открывает страницу по file:// и узнаёт адрес у оболочки
 * (preload, window.electronAPI.backendUrl): порт выбирается при запуске, 8000 может быть занят.
 * Иначе — VITE_API_URL сборки; пусто — относительные пути через прокси Vite / тот же хост.
 */
type ElectronBridge = { electronAPI?: { backendUrl?: string | null } };

export function backendUrl(): string | undefined {
  const fromShell = typeof window !== 'undefined' ? (window as unknown as ElectronBridge).electronAPI?.backendUrl : undefined;
  return fromShell || (import.meta.env.VITE_API_URL as string | undefined) || undefined;
}
