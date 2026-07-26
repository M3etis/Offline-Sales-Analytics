import { app, BrowserWindow, dialog } from 'electron';
import path from 'path';
import { startBackend, stopBackend } from './backend';
import { startOllama, stopOllama } from './ollama-manager';
import {
  registerIpcHandlers,
  setRuntimeContext,
  setBackendRunning,
  setOllamaStatus,
} from './ipc/handlers';

const isDev = !app.isPackaged;

let mainWindow: BrowserWindow | null = null;

function getProjectRoot(): string {
  // In dev mode: __dirname is desktop/dist-electron/main, so go up 3 levels to project root
  // In production: resourcesPath contains the bundled app
  return isDev ? path.resolve(__dirname, '..', '..', '..') : process.resourcesPath;
}

function getFrontendPath(): string {
  return isDev
    ? 'http://127.0.0.1:5173'
    : `file://${path.join(process.resourcesPath, 'frontend', 'index.html')}`;
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    show: false,
    title: 'Sales Analytics',
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

  await mainWindow.loadURL(getFrontendPath());
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

  app.on('before-quit', async () => {
    await stopBackend();
    await stopOllama();
  });

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });

  await app.whenReady();

  // Start Ollama first
  console.log('[main] Starting Ollama...');
  const ollamaReady = await startOllama({ isDev, resourcesPath: process.resourcesPath });
  setOllamaStatus(ollamaReady);
  if (!ollamaReady) {
    console.log('[main] Ollama not available. AI features will be limited.');
  }

  // Start backend
  console.log('[main] Starting backend...');
  const backendReady = await startBackend({ isDev, projectRoot, resourcesPath: process.resourcesPath });
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
        '- Порт 8000 не занят другим приложением\n' +
        '- Python и зависимости установлены\n' +
        '- Файл .env существует и настроен\n\n' +
        'Приложение будет закрыто.',
      buttons: ['OK'],
    });
    app.quit();
    return;
  }

  console.log('[main] Backend is ready.');
  await createWindow();

  if (!ollamaReady && mainWindow) {
    await dialog.showMessageBox(mainWindow, {
      type: 'warning',
      title: 'AI недоступен',
      message: 'Ollama не запущена',
      detail:
        'AI-ассистент будет недоступен.\n\n' +
        'Для работы с ИИ:\n' +
        '1. Установите Ollama: https://ollama.com\n' +
        '2. Скачайте модель: ollama pull qwen2.5:14b\n\n' +
        'Приложение продолжит работу без AI-функций.',
      buttons: ['Понятно'],
    });
  }
}

main().catch((err) => {
  console.error('[main] Fatal error:', err);
  app.quit();
});
