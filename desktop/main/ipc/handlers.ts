/**
 * IPC handlers for the Electron main process.
 *
 * Registered once in index.ts after app.whenReady().
 * Exposes backend/ollama status and restart capabilities to the renderer.
 */
import { app, ipcMain, BrowserWindow } from 'electron';
import { getBackendPort, startBackend, stopBackend } from '../backend';
import { startOllama } from '../ollama-manager';
import { pollHealth } from '../health';

let _isDev = false;
let _projectRoot = '';
let _resourcesPath = '';
let _backendRunning = false;
let _ollamaRunning = false;
let _ollamaModel: string | null = null;

const MAX_RESTART_ATTEMPTS = 3;
let _restartAttempts = 0;

export function setRuntimeContext(ctx: {
  isDev: boolean;
  projectRoot: string;
  resourcesPath: string;
}) {
  _isDev = ctx.isDev;
  _projectRoot = ctx.projectRoot;
  _resourcesPath = ctx.resourcesPath;
}

export function setBackendRunning(running: boolean) {
  _backendRunning = running;
  BrowserWindow.getAllWindows().forEach((w) =>
    w.webContents.send('backend:status-change', { running })
  );
}

export function setOllamaStatus(running: boolean, model: string | null = null) {
  _ollamaRunning = running;
  _ollamaModel = model;
}

export function registerIpcHandlers() {
  // ── backend:status ─────────────────────────────────────────────
  ipcMain.handle('backend:status', () => ({
    running: _backendRunning,
    port: getBackendPort(),
  }));

  // ── backend:url (синхронно, читает preload до скриптов страницы) ─
  // Сборка открывает страницу по file:// и обращается к бэкенду по абсолютному адресу; порт
  // выбирается при запуске. В dev — null: запросы идут через прокси Vite
  ipcMain.on('backend:url', (event) => {
    event.returnValue = _isDev ? null : `http://127.0.0.1:${getBackendPort()}`;
  });

  // ── ollama:status ──────────────────────────────────────────────
  ipcMain.handle('ollama:status', () => ({
    running: _ollamaRunning,
    model: _ollamaModel,
  }));

  // ── backend:restart ────────────────────────────────────────────
  ipcMain.handle('backend:restart', async () => {
    if (_restartAttempts >= MAX_RESTART_ATTEMPTS) {
      return { success: false, error: `Max restart attempts (${MAX_RESTART_ATTEMPTS}) reached.` };
    }
    _restartAttempts++;
    console.log(`[ipc] Restarting backend (attempt ${_restartAttempts}/${MAX_RESTART_ATTEMPTS})...`);
    try {
      const portBefore = getBackendPort();
      await stopBackend();
      const ready = await startBackend({
        isDev: _isDev,
        projectRoot: _projectRoot,
        resourcesPath: _resourcesPath,
        userDataPath: app.getPath('userData'),
      });
      setBackendRunning(ready);
      if (ready) _restartAttempts = 0;
      // Прежний порт заняли — страница знает старый адрес: перезагружаем её с новым
      if (ready && getBackendPort() !== portBefore) {
        BrowserWindow.getAllWindows().forEach((w) => w.webContents.reload());
      }
      return { success: ready, error: ready ? undefined : 'Backend failed health check' };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      return { success: false, error: msg };
    }
  });
}
