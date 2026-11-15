import { app, BrowserWindow, dialog } from 'electron';
import path from 'path';
import { startBackend, stopBackend } from './backend';
import { readBundledModel, startOllama, stopOllama } from './ollama-manager';
import {
  registerIpcHandlers,
  setRuntimeContext,
  setBackendRunning,
  setOllamaStatus,
} from './ipc/handlers';

const isDev = !app.isPackaged;

let mainWindow: BrowserWindow | null = null;
let splashWindow: BrowserWindow | null = null;

const SPLASH_HTML = `<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Offline Sales Analytics</title>
<style>
  :root { color-scheme: light dark; --bg: #fff; --fg: #171717; --muted: #6e6e73; --track: #e5e5e5; --accent: #9a5b00; }
  @media (prefers-color-scheme: dark) { :root { --bg: #1c1c1e; --fg: #f5f5f7; --muted: #a1a1a6; --track: #3a3a3c; --accent: #e0a040; } }
  html, body { margin: 0; height: 100%; background: var(--bg); color: var(--fg); -webkit-user-select: none; user-select: none; }
  body { display: flex; flex-direction: column; justify-content: center; gap: 10px; padding: 0 28px;
    font: 13px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif; -webkit-app-region: drag; }
  h1 { margin: 0; font-size: 15px; font-weight: 600; }
  p { margin: 0; color: var(--muted); }
  .bar { height: 3px; border-radius: 2px; background: var(--track); overflow: hidden; }
  .bar::after { content: ""; display: block; width: 30%; height: 100%; background: var(--accent); animation: run 1.4s ease-in-out infinite; }
  @keyframes run { from { transform: translateX(-100%); } to { transform: translateX(340%); } }
</style></head><body>
  <h1>Offline Sales Analytics</h1>
  <p id="status">Запуск…</p>
  <div class="bar"></div>
</body></html>`;

/**
 * Окно запуска: пока поднимаются Ollama и бэкенд, основного окна ещё нет. При первом запуске
 * собранного приложения встроенная модель (несколько ГБ) может копироваться минутами — без этого
 * окна казалось бы, что приложение не открылось.
 */
function showSplash() {
  splashWindow = new BrowserWindow({
    width: 420,
    height: 150,
    frame: false,
    resizable: false,
    show: false,
    center: true,
    title: 'Offline Sales Analytics',
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  splashWindow.once('ready-to-show', () => splashWindow?.show());
  splashWindow.on('closed', () => {
    splashWindow = null;
  });
  void splashWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(SPLASH_HTML)}`);
}

function setSplashStatus(text: string) {
  splashWindow?.webContents
    .executeJavaScript(`document.getElementById('status').textContent = ${JSON.stringify(text)}`)
    .catch(() => {});
}

/** Закрывать только когда уже есть другое окно, иначе сработает window-all-closed и приложение выйдет */
function closeSplash() {
  splashWindow?.destroy();
  splashWindow = null;
}

function getProjectRoot(): string {
  // In dev mode: __dirname is desktop/dist-electron/main, so go up 3 levels to project root
  // In production: resourcesPath contains the bundled app
  return isDev ? path.resolve(__dirname, '..', '..', '..') : process.resourcesPath;
}

/** Открыть интерфейс: в dev — сервер Vite, в сборке — файл (loadFile сам кодирует путь:
 * обратные слэши Windows, пробелы, «#» и «%» в пути установки ломали ручной file://) */
// Сервер Vite в dev; другой порт — через ELECTRON_DEV_URL
const DEV_URL = process.env.ELECTRON_DEV_URL || 'http://127.0.0.1:5173';

function loadFrontend(win: BrowserWindow): Promise<void> {
  return isDev
    ? win.loadURL(DEV_URL)
    : win.loadFile(path.join(process.resourcesPath, 'frontend', 'index.html'));
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    show: false,
    title: 'Offline Sales Analytics',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  await loadFrontend(mainWindow);
  mainWindow.show();

  if (isDev) {
    mainWindow.webContents.openDevTools();
  }
}

async function main() {
  const projectRoot = getProjectRoot();

  console.log(`[main] Starting in ${isDev ? 'DEV' : 'PRODUCTION'} mode`);
  console.log(`[main] Project root: ${projectRoot}`);
  console.log(`[main] Resources path: ${process.resourcesPath}`);

  // Register IPC handlers before window creation
  setRuntimeContext({ isDev, projectRoot, resourcesPath: process.resourcesPath });
  registerIpcHandlers();

  app.on('window-all-closed', async () => {
    await stopBackend();
    await stopOllama();
    app.quit();
  });

  // Electron не ждёт async-обработчик: без preventDefault приложение выходило раньше, чем
  // останавливались службы, и `ollama serve` оставался висеть. Останавливаем и выходим ещё раз
  let servicesStopped = false;
  app.on('before-quit', (event) => {
    if (servicesStopped) return;
    event.preventDefault();
    servicesStopped = true;
    void (async () => {
      try {
        await stopBackend();
        await stopOllama();
      } finally {
        app.quit();
      }
    })();
  });

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });

  await app.whenReady();
  showSplash();

  // Start Ollama first
  console.log('[main] Starting Ollama...');
  setSplashStatus(
    isDev ? 'Запуск Ollama…' : 'Подготовка модели ИИ… При первом запуске это может занять несколько минут.',
  );
  const ollamaReady = await startOllama({ isDev, resourcesPath: process.resourcesPath });
  setOllamaStatus(ollamaReady, isDev ? null : readBundledModel(process.resourcesPath));
  if (!ollamaReady) {
    console.log('[main] Ollama not available. AI features will be limited.');
  }

  // Start backend
  console.log('[main] Starting backend...');
  setSplashStatus('Запуск сервера…');
  const backendReady = await startBackend({
    isDev,
    projectRoot,
    resourcesPath: process.resourcesPath,
    userDataPath: app.getPath('userData'),
  });
  setBackendRunning(backendReady);

  if (!backendReady) {
    console.error('[main] Backend failed to start!');
    await dialog.showMessageBox({
      type: 'error',
      title: 'Ошибка запуска',
      message: 'Backend не удалось запустить',
      detail:
        'Сервер не ответил на проверку здоровья за 30 секунд.\n\n' +
        'Проверьте:\n' +
        (isDev
          ? `- Порт ${process.env.BACKEND_PORT || 8000} не занят другим приложением (в dev он нужен прокси Vite)\n`
          : '- Есть свободный порт в диапазоне 8000–8099\n') +
        '- Python и зависимости установлены\n' +
        '- Файл .env существует и настроен\n\n' +
        'Приложение будет закрыто.',
      buttons: ['OK'],
    });
    app.quit();
    return;
  }

  console.log('[main] Backend is ready.');
  setSplashStatus('Открываю приложение…');
  try {
    await createWindow();
  } finally {
    closeSplash();
  }

  if (!ollamaReady && mainWindow) {
    await dialog.showMessageBox(mainWindow, {
      type: 'warning',
      title: 'AI недоступен',
      message: 'Ollama не запущена',
      detail:
        'AI-ассистент будет недоступен.\n\n' +
        'Для работы с ИИ:\n' +
        '1. Установите Ollama: https://ollama.com\n' +
        '2. Откройте «Настройки → Настройки ассистента» и установите рекомендуемую модель\n\n' +
        'Приложение продолжит работу без AI-функций.',
      buttons: ['Понятно'],
    });
  }
}

// Один экземпляр: второй запуск поднимал бы свой бэкенд со своей копией состояния — две копии
// приложения на одной базе DuckDB не уживаются
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
  main().catch((err) => {
    console.error('[main] Fatal error:', err);
    app.quit();
  });
}
