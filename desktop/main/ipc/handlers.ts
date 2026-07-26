/**
 * IPC handlers for the Electron main process.
 *
 * Registered once in index.ts after app.whenReady().
 * Exposes backend/ollama status and restart capabilities to the renderer.
 */
import { ipcMain, BrowserWindow } from 'electron';
import { startBackend, stopBackend } from '../backend';
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
    port: 8000,
  }));

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
      await stopBackend();
      const ready = await startBackend({
        isDev: _isDev,
        projectRoot: _projectRoot,
        resourcesPath: _resourcesPath,
      });
      setBackendRunning(ready);
      if (ready) _restartAttempts = 0;
      return { success: ready, error: ready ? undefined : 'Backend failed health check' };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      return { success: false, error: msg };
    }
  });
}
