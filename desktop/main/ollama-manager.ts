import { ChildProcess, spawn, execSync } from 'child_process';
import path from 'path';
import os from 'os';
import fs from 'fs';
import http from 'http';

let ollamaProcess: ChildProcess | null = null;
let ollamaStartedByUs = false;

export interface OllamaManagerOptions {
  isDev: boolean;
  resourcesPath: string;
}

function log(data: Buffer) {
  const lines = data.toString().trim().split('\n');
  for (const line of lines) {
    console.log(`[ollama] ${line}`);
  }
}

export function checkOllama(): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get('http://127.0.0.1:11434/api/tags', { timeout: 2000 }, (res) => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

function waitForOllama(maxAttempts = 30): Promise<boolean> {
  return new Promise((resolve) => {
    let attempts = 0;
    const check = () => {
      attempts++;
      const req = http.get('http://127.0.0.1:11434/api/tags', { timeout: 1000 }, (res) => {
        res.resume();
        if (res.statusCode === 200) resolve(true);
        else if (attempts < maxAttempts) setTimeout(check, 1000);
        else resolve(false);
      });
      req.on('error', () => {
        if (attempts < maxAttempts) setTimeout(check, 1000);
        else resolve(false);
      });
      req.on('timeout', () => {
        req.destroy();
        if (attempts < maxAttempts) setTimeout(check, 1000);
        else resolve(false);
      });
    };
    check();
  });
}

function findOllamaBinary(resourcesPath: string): string | null {
  const arch = os.arch();
  const isWin = process.platform === 'win32';
  const ext = isWin ? '.exe' : '';
  const binaryName = `ollama${ext}`;

  // Try bundled binary in resources
  const bundledPath = path.join(resourcesPath, 'ollama', arch, binaryName);
  if (fs.existsSync(bundledPath)) return bundledPath;

  const genericPath = path.join(resourcesPath, 'ollama', binaryName);
  if (fs.existsSync(genericPath)) return genericPath;

  // Try system-installed Ollama
  if (!isWin) {
    const systemPaths = ['/usr/local/bin/ollama', '/opt/homebrew/bin/ollama'];
    for (const p of systemPaths) {
      if (fs.existsSync(p)) return p;
    }
    try {
      const result = execSync('which ollama', { encoding: 'utf8', timeout: 3000 }).trim();
      if (result) return result;
    } catch { /* not found */ }
  } else {
    try {
      const result = execSync('where ollama', { encoding: 'utf8', timeout: 3000 }).trim();
      if (result) return result.split('\n')[0].trim();
    } catch { /* not found */ }
  }

  return null;
}

export async function startOllama(options: OllamaManagerOptions): Promise<boolean> {
  const { isDev, resourcesPath } = options;

  // Check if Ollama is already running (external installation)
  const alreadyRunning = await checkOllama();
  if (alreadyRunning) {
    console.log('[ollama] Already running externally.');
    return true;
  }

  // Find Ollama binary
  const binaryPath = findOllamaBinary(resourcesPath);
  if (!binaryPath) {
    console.log('[ollama] Binary not found. AI features will be unavailable.');
    return false;
  }

  console.log(`[ollama] Starting: ${binaryPath}`);

  // In production, use bundled models. In dev, use user's home.
  const bundledModelsDir = path.join(resourcesPath, 'ollama-models');
  const userModelsDir = path.join(os.homedir(), '.ollama', 'models');

  // Use bundled if it has models, otherwise fall back to user's dir
  const bundledManifests = path.join(bundledModelsDir, 'manifests');
  const ollamaModelsDir = (!isDev && fs.existsSync(bundledManifests))
    ? bundledModelsDir
    : userModelsDir;

  console.log(`[ollama] Models dir: ${ollamaModelsDir}`);

  // Ensure models directory exists
  fs.mkdirSync(ollamaModelsDir, { recursive: true });

  ollamaProcess = spawn(binaryPath, ['serve'], {
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      OLLAMA_MODELS: ollamaModelsDir,
      OLLAMA_HOST: '127.0.0.1:11434',
    },
  });

  ollamaProcess.stdout?.on('data', log);
  ollamaProcess.stderr?.on('data', log);

  ollamaProcess.on('error', (err) => {
    console.error('[ollama] Failed to start:', err.message);
    ollamaProcess = null;
  });

  ollamaProcess.on('exit', (code, signal) => {
    console.log(`[ollama] Exited with code ${code}, signal ${signal}`);
    ollamaProcess = null;
    ollamaStartedByUs = false;
  });

  const ready = await waitForOllama();
  if (ready) {
    ollamaStartedByUs = true;
    console.log('[ollama] Started successfully.');
  } else {
    console.log('[ollama] Failed to start within timeout.');
    if (ollamaProcess) {
      try { ollamaProcess.kill('SIGKILL'); } catch { /* ignore */ }
      ollamaProcess = null;
    }
  }

  return ready;
}

export function stopOllama(): Promise<void> {
  return new Promise((resolve) => {
    if (!ollamaProcess || !ollamaStartedByUs) {
      resolve();
      return;
    }

    console.log('[ollama] Stopping...');

    const timeout = setTimeout(() => {
      console.log('[ollama] Force killing...');
      try {
        if (ollamaProcess && !ollamaProcess.killed) {
          ollamaProcess.kill('SIGKILL');
        }
      } catch { /* ignore */ }
      ollamaProcess = null;
      ollamaStartedByUs = false;
      resolve();
    }, 5000);

    ollamaProcess.on('exit', () => {
      clearTimeout(timeout);
      ollamaProcess = null;
      ollamaStartedByUs = false;
      console.log('[ollama] Stopped.');
      resolve();
    });

    try {
      ollamaProcess.kill('SIGTERM');
    } catch {
      clearTimeout(timeout);
      ollamaProcess = null;
      ollamaStartedByUs = false;
      resolve();
    }
  });
}
