/**
 * Навигация, одинаковая для веба и desktop-приложения.
 *
 * Electron открывает собранный фронтенд из файла (file://…/index.html). Обычные пути там не работают:
 * переход на «/dashboard» ведёт на несуществующий file:///dashboard. Поэтому при file:// маршруты
 * живут в hash (#/dashboard), а в вебе — как обычно, в пути.
 */
export const isFileProtocol = typeof window !== 'undefined' && window.location.protocol === 'file:';

/** Текущий маршрут приложения («/login», «/dashboard») — в вебе из пути, в desktop из hash */
export function currentAppPath(): string {
  return isFileProtocol ? window.location.hash.replace(/^#/, '') || '/' : window.location.pathname;
}

/** Переход с полной перезагрузкой страницы: сбрасывает всё состояние в памяти (после выхода, сброса данных) */
export function hardNavigate(path: string): void {
  if (isFileProtocol) {
    window.location.hash = path;
    window.location.reload();
  } else {
    window.location.href = path;
  }
}
