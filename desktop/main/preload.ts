/**
 * Electron preload script.
 *
 * Exposes a typed `window.electronAPI` to the renderer process
 * via contextBridge (contextIsolation=true, nodeIntegration=false).
 */
import { contextBridge, ipcRenderer } from 'electron';

export interface ElectronAPI {
  /** Returns the current backend status */
  getBackendStatus: () => Promise<{ running: boolean; port: number }>;
  /** Returns the current Ollama status */
  getOllamaStatus: () => Promise<{ running: boolean; model: string | null }>;
  /** Ask the main process to restart the backend */
  restartBackend: () => Promise<{ success: boolean; error?: string }>;
  /** Subscribe to backend status changes */
  onBackendStatus: (cb: (status: { running: boolean }) => void) => () => void;
}

const api: ElectronAPI = {
  getBackendStatus: () => ipcRenderer.invoke('backend:status'),
  getOllamaStatus:  () => ipcRenderer.invoke('ollama:status'),
  restartBackend:   () => ipcRenderer.invoke('backend:restart'),
  onBackendStatus: (cb) => {
    const handler = (_: Electron.IpcRendererEvent, status: { running: boolean }) => cb(status);
    ipcRenderer.on('backend:status-change', handler);
    // Return cleanup function
    return () => ipcRenderer.off('backend:status-change', handler);
  },
};

contextBridge.exposeInMainWorld('electronAPI', api);

// Type declaration for renderer (consumed by TypeScript)
declare global {
  interface Window {
    electronAPI: ElectronAPI;
  }
}
