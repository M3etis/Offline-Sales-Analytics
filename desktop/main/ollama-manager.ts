import { ChildProcess, spawn, execSync } from 'child_process';
import path from 'path';
import os from 'os';
import fs from 'fs';
import http from 'http';
import { pollHealth } from './health';
import { seedBundledModels } from './model-seed';

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
  // Тот же опрос, что и для бэкенда: одна попытка — один повтор (см. health.ts)
  return pollHealth({ url: 'http://127.0.0.1:11434/api/tags', intervalMs: 1000, maxAttempts, timeoutMs: 1000 });
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

/**
 * Модель, встроенная в сборку (desktop/scripts/bundle-model.py пишет её имя в bundle.json).
 * null — сборка без модели или собрана до появления bundle.json.
 */
export function readBundledModel(resourcesPath: string): string | null {
  try {
    const raw = fs.readFileSync(path.join(resourcesPath, 'ollama-models', 'bundle.json'), 'utf8');
    const meta = JSON.parse(raw) as { model?: unknown };
    return typeof meta.model === 'string' && meta.model.trim() ? meta.model.trim() : null;
  } catch {
    return null;
  }
}

/** Папка моделей пользователя: туда же скачивает модели обычная установка Ollama */
function userModelsDir(): string {
  return process.env.OLLAMA_MODELS || path.join(os.homedir(), '.ollama', 'models');
}

export async function startOllama(options: OllamaManagerOptions): Promise<boolean> {
  const { isDev, resourcesPath } = options;

  // Модели живут в папке пользователя, чтобы их можно было докачивать из интерфейса.
  // Встроенную в сборку модель прописываем туда до запуска — её увидит и уже запущенная Ollama.
  let ollamaModelsDir = userModelsDir();
  if (!isDev) {
    const seeded = await seedBundledModels(resourcesPath, ollamaModelsDir);
    const bundledModelsDir = path.join(resourcesPath, 'ollama-models');
    if (!seeded && fs.existsSync(path.join(bundledModelsDir, 'manifests'))) {
      // Запасной путь: хотя бы встроенная модель будет работать, но докачать другие не выйдет
      console.log('[ollama] Falling back to bundled models dir (read-only).');
      ollamaModelsDir = bundledModelsDir;
    }
  }

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
  console.log(`[ollama] Models dir: ${ollamaModelsDir}`);

  // Ensure models directory exists
  fs.mkdirSync(ollamaModelsDir, { recursive: true });
  // Бэкенд наследует окружение и по этой переменной считает свободное место для загрузки моделей
  process.env.OLLAMA_MODELS = ollamaModelsDir;

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
  // Процесс наш с момента запуска, а не после ответа: выход во время заставки (до 30 с ожидания)
  // иначе оставлял `ollama serve` работать, а следующий запуск считал его чужим и не гасил
  ollamaStartedByUs = true;

  const ready = await waitForOllama();
  if (ready) {
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
