import { ChildProcess, spawn, execSync, execFileSync } from 'child_process';
import path from 'path';
import os from 'os';
import fs from 'fs';
import crypto from 'crypto';
import net from 'net';
import { pollHealth } from './health';
import { readBundledModel } from './ollama-manager';

let backendProcess: ChildProcess | null = null;

/** Порт по умолчанию; в сборке, если он занят, берём ближайший свободный (как start.sh) */
const DEFAULT_PORT = 8000;
const PORT_SCAN = 100;
let backendPort = DEFAULT_PORT;

/** Порт, на котором запущен (или запускается) бэкенд */
export function getBackendPort(): number {
  return backendPort;
}

function isPortFree(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.once('error', () => resolve(false));
    srv.once('listening', () => srv.close(() => resolve(true)));
    srv.listen(port, '127.0.0.1');
  });
}

/**
 * Свободный порт начиная с preferred. Раньше бэкенд всегда занимал 8000: если порт держало другое
 * приложение, uvicorn падал, а проверка здоровья могла получить 200 от чужого сервера
 * (например, веб-версии этого же приложения со своей базой) — окно работало не с теми данными.
 */
export async function findFreePort(preferred: number): Promise<number | null> {
  for (let port = preferred; port < preferred + PORT_SCAN; port++) {
    if (await isPortFree(port)) return port;
  }
  return null;
}

function healthUrl(): string {
  return `http://127.0.0.1:${backendPort}/health`;
}

/**
 * Данные и логи — в userData, а не внутри пакета приложения: обновление заменяло Resources
 * вместе с базой (пользователи, загрузки, история), а на установке только для чтения
 * (Program Files, App Translocation) mkdir падал и приложение закрывалось без сообщения.
 */
function runtimeDirs(userDataPath: string): { dataDir: string; logsDir: string } {
  return { dataDir: path.join(userDataPath, 'data'), logsDir: path.join(userDataPath, 'logs') };
}

function getLogFile(logsDir: string, name: string): fs.WriteStream {
  fs.mkdirSync(logsDir, { recursive: true });
  return fs.createWriteStream(path.join(logsDir, name), { flags: 'a' });
}

/** Окружение бэкенда в упакованном приложении */
function prodEnv(resourcesPath: string, userDataPath: string): NodeJS.ProcessEnv {
  const { dataDir, logsDir } = runtimeDirs(userDataPath);
  return {
    ...process.env,
    DB_PATH: path.join(dataDir, 'processed', 'sales.duckdb'),
    DATA_DIR: dataDir,
    LOG_DIR: logsDir,
    // Страница открыта по file:// — Chromium шлёт Origin: null, и без него в списке
    // CORS отклонял preflight-запросы (вход, загрузка). Токен идёт в заголовке, не в cookie
    ALLOWED_ORIGINS: [process.env.ALLOWED_ORIGINS, 'null', 'file://'].filter(Boolean).join(','),
    ...bundledModelEnv(resourcesPath),
    SECRET_KEY: loadSecretKey(userDataPath),
  };
}

/** Встроенная в сборку модель становится моделью по умолчанию (выбор в интерфейсе приложения важнее) */
function bundledModelEnv(resourcesPath: string): Record<string, string> {
  const model = readBundledModel(resourcesPath);
  return model ? { OLLAMA_MODEL: model } : {};
}

export interface BackendStartOptions {
  isDev: boolean;
  projectRoot: string;
  resourcesPath: string;
  /** app.getPath('userData') — каталог данных пользователя, переживает обновления приложения */
  userDataPath: string;
}

/**
 * Ключ подписи токенов — свой на каждой установке, хранится в userData.
 * Раньше во всех сборках был один ключ, зашитый в исходники: любой мог выпустить себе
 * токен администратора для чужой установки.
 */
export function loadSecretKey(userDataPath: string): string {
  if (process.env.SECRET_KEY && process.env.SECRET_KEY.length >= 32) return process.env.SECRET_KEY;
  const keyFile = path.join(userDataPath, 'secret.key');
  try {
    const existing = fs.readFileSync(keyFile, 'utf8').trim();
    if (existing.length >= 32) return existing;
  } catch { /* файла ещё нет */ }
  const key = crypto.randomBytes(32).toString('hex');
  fs.mkdirSync(userDataPath, { recursive: true });
  fs.writeFileSync(keyFile, key, { mode: 0o600 });
  return key;
}

function log(prefix: string, data: Buffer) {
  const lines = data.toString().trim().split('\n');
  for (const line of lines) {
    console.log(`[backend] ${line}`);
  }
}

export async function startBackend(options: BackendStartOptions): Promise<boolean> {
  const { isDev, projectRoot, resourcesPath, userDataPath } = options;

  if (isDev) {
    return startBackendDev(projectRoot);
  } else {
    return startBackendProd(resourcesPath, userDataPath);
  }
}

async function startBackendDev(projectRoot: string): Promise<boolean> {
  const venvPython = process.platform === 'win32'
    ? path.join(projectRoot, 'venv', 'Scripts', 'python.exe')
    : path.join(projectRoot, 'venv', 'bin', 'python');

  // В dev интерфейс ходит в бэкенд через прокси Vite, а он настроен на BACKEND_PORT (vite.config.ts):
  // порт менять нельзя. Занят — это бэкенд, поднятый скриптом (dev.ps1 запускает его сам), если
  // отвечает /health; иначе — чужое приложение, и запускаться некуда
  const envPort = Number(process.env.BACKEND_PORT);
  backendPort = Number.isInteger(envPort) && envPort > 0 ? envPort : DEFAULT_PORT;
  if (!(await isPortFree(backendPort))) {
    if (await pollHealth({ url: healthUrl(), maxAttempts: 1 })) {
      console.log(`[backend] Dev backend is already running on port ${backendPort}, reusing it.`);
      return true;
    }
    console.error(`[backend] Port ${backendPort} is busy; dev mode needs it for the Vite proxy (BACKEND_PORT).`);
    return false;
  }

  console.log(`[backend] Starting FastAPI in dev mode on port ${backendPort}...`);

  backendProcess = spawn(venvPython, [
    '-m', 'uvicorn',
    'app.main:app',
    '--host', '127.0.0.1',
    '--port', String(backendPort),
  ], {
    cwd: projectRoot,
    stdio: ['ignore', 'pipe', 'pipe'],
    // SECRET_KEY не подставляем: в dev его берёт из окружения или .env сам бэкенд.
    // Раньше подставлялся ключ, зашитый в исходники, — он перекрывал .env
    env: { ...process.env },
  });

  backendProcess.stdout?.on('data', (data: Buffer) => log('stdout', data));
  backendProcess.stderr?.on('data', (data: Buffer) => log('stderr', data));

  backendProcess.on('error', (err) => {
    console.error('[backend] Failed to start:', err.message);
  });

  backendProcess.on('exit', (code, signal) => {
    console.log(`[backend] Exited with code ${code}, signal ${signal}`);
    backendProcess = null;
  });

  return pollHealth({
    url: healthUrl(),
    intervalMs: 500,
    maxAttempts: 60,
  });
}

function getBackendBinary(resourcesPath: string): string {
  const arch = os.arch();
  const isWin = process.platform === 'win32';
  const ext = isWin ? '.exe' : '';
  const binaryName = `sales-analytics-backend${ext}`;

  const archDir = path.join(resourcesPath, 'backend', arch);
  const genericDir = path.join(resourcesPath, 'backend');

  if (fs.existsSync(path.join(archDir, binaryName))) {
    return path.join(archDir, binaryName);
  }

  return path.join(genericDir, binaryName);
}

// Поддерживаемые версии — как в start.sh: на Python 3.14+ pandas 2.x падает с segfault
const PY_VERSION_CHECK = 'import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)';

function isSupportedPython(cmd: string): boolean {
  try {
    execFileSync(cmd, ['-c', PY_VERSION_CHECK], { timeout: 5000, stdio: 'ignore' });
    return true;
  } catch {
    return false; // нет такого интерпретатора или версия вне диапазона
  }
}

function findPython(): string | null {
  if (process.platform === 'win32') {
    const candidates = ['python', 'python3'];
    for (const cmd of candidates) {
      try {
        const result = execSync(`where ${cmd}`, { encoding: 'utf8', timeout: 3000 }).trim();
        // where перечисляет все найденные копии — берём первую подходящей версии
        const supported = result.split('\n').map((line) => line.trim()).find((p) => p && isSupportedPython(p));
        if (supported) return supported;
      } catch { /* not found */ }
    }
    // Лаунчер py без ключа запускает самую новую версию (может быть 3.14) — спрашиваем нужные явно
    // и берём путь к самому интерпретатору: backend запускается по пути, без аргументов лаунчера
    for (const version of ['-3.12', '-3.13', '-3.11', '-3.10']) {
      try {
        const exe = execFileSync('py', [version, '-c', 'import sys; print(sys.executable)'], {
          encoding: 'utf8',
          timeout: 5000,
          stdio: ['ignore', 'pipe', 'ignore'],
        }).trim();
        if (exe && isSupportedPython(exe)) return exe;
      } catch { /* лаунчера нет или такой версии нет */ }
    }
  } else {
    // Сначала версии с номером: python3 часто указывает на самый новый (3.14+)
    const versioned = ['python3.12', 'python3.13', 'python3.11', 'python3.10'];
    const candidates = [
      ...versioned.map((v) => `/opt/homebrew/bin/${v}`),
      ...versioned.map((v) => `/usr/local/bin/${v}`),
      ...versioned,
      '/opt/homebrew/bin/python3',
      '/usr/local/bin/python3',
      '/usr/bin/python3',
      'python3',
    ];
    for (const cmd of candidates) {
      if (isSupportedPython(cmd)) return cmd;
    }
  }
  return null;
}

function startBackendWithBinary(binaryPath: string, resourcesPath: string, userDataPath: string): Promise<boolean> {
  const backendDir = path.dirname(binaryPath);
  const logStream = getLogFile(runtimeDirs(userDataPath).logsDir, 'backend.log');

  console.log(`[backend] Starting binary: ${binaryPath}`);

  backendProcess = spawn(binaryPath, [], {
    cwd: backendDir,
    stdio: ['ignore', 'pipe', 'pipe'],
    // Порт бинарник берёт из BACKEND_PORT (app/main.py)
    env: { ...prodEnv(resourcesPath, userDataPath), BACKEND_PORT: String(backendPort) },
  });

  backendProcess.stdout?.on('data', (data: Buffer) => { log('stdout', data); logStream.write(data); });
  backendProcess.stderr?.on('data', (data: Buffer) => { log('stderr', data); logStream.write(data); });

  backendProcess.on('error', (err) => {
    console.error('[backend] Binary failed:', err.message);
  });

  backendProcess.on('exit', (code, signal) => {
    console.log(`[backend] Exited with code ${code}, signal ${signal}`);
    logStream.end();
    backendProcess = null;
  });

  return pollHealth({
    url: healthUrl(),
    intervalMs: 500,
    maxAttempts: 30,
  });
}

function startBackendWithPython(pythonPath: string, resourcesPath: string, userDataPath: string): Promise<boolean> {
  const appDir = path.join(resourcesPath, 'app');
  const logStream = getLogFile(runtimeDirs(userDataPath).logsDir, 'backend.log');

  console.log(`[backend] Falling back to Python: ${pythonPath}`);
  console.log(`[backend] App dir: ${appDir}`);

  backendProcess = spawn(pythonPath, [
    '-m', 'uvicorn',
    'app.main:app',
    '--host', '127.0.0.1',
    '--port', String(backendPort),
  ], {
    cwd: resourcesPath,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...prodEnv(resourcesPath, userDataPath), PYTHONPATH: resourcesPath },
  });

  backendProcess.stdout?.on('data', (data: Buffer) => { log('stdout', data); logStream.write(data); });
  backendProcess.stderr?.on('data', (data: Buffer) => { log('stderr', data); logStream.write(data); });

  backendProcess.on('error', (err) => {
    console.error('[backend] Python fallback failed:', err.message);
  });

  backendProcess.on('exit', (code, signal) => {
    console.log(`[backend] Exited with code ${code}, signal ${signal}`);
    logStream.end();
    backendProcess = null;
  });

  return pollHealth({
    url: healthUrl(),
    intervalMs: 500,
    maxAttempts: 60,
  });
}

async function startBackendProd(resourcesPath: string, userDataPath: string): Promise<boolean> {
  const binaryPath = getBackendBinary(resourcesPath);
  const { dataDir } = runtimeDirs(userDataPath);

  // При перезапуске — прежний порт, если он освободился: страница уже знает этот адрес
  const port = await findFreePort(backendPort);
  if (port === null) {
    console.error(`[backend] No free port in ${backendPort}–${backendPort + PORT_SCAN - 1}.`);
    return false;
  }
  if (port !== DEFAULT_PORT) console.log(`[backend] Port ${DEFAULT_PORT} is busy, using ${port}.`);
  backendPort = port;

  console.log(`[backend] Architecture: ${os.arch()}`);
  console.log(`[backend] Data dir: ${dataDir}`);

  // Try native binary first
  if (fs.existsSync(binaryPath)) {
    console.log(`[backend] Trying native binary...`);
    const ready = await startBackendWithBinary(binaryPath, resourcesPath, userDataPath);

    if (ready) {
      console.log('[backend] Native binary started successfully.');
      return true;
    }

    console.log('[backend] Native binary failed to respond. Checking if it crashed...');

    // Check if process is still alive (might be running under Rosetta slowly)
    if (backendProcess && !backendProcess.killed) {
      // Give it more time under Rosetta
      console.log('[backend] Process still alive, waiting longer (Rosetta)...');
      const rosettaReady = await pollHealth({
        url: healthUrl(),
        intervalMs: 1000,
        maxAttempts: 30,
      });
      if (rosettaReady) return true;
    }

    // Binary failed, clean up
    if (backendProcess) {
      try { backendProcess.kill('SIGKILL'); } catch { /* ignore */ }
      backendProcess = null;
    }
  }

  // Fallback to Python
  console.log('[backend] Binary unavailable or failed. Trying Python fallback...');
  const python = findPython();

  if (!python) {
    console.error('[backend] No supported Python (3.10–3.13) found! Cannot start backend.');
    return false;
  }

  console.log(`[backend] Found Python: ${python}`);

  // Check if app source exists for fallback
  const appDir = path.join(resourcesPath, 'app');
  if (!fs.existsSync(appDir)) {
    console.error('[backend] App source not found at:', appDir);
    console.error('[backend] Python fallback requires app/ source in resources.');
    return false;
  }

  return startBackendWithPython(python, resourcesPath, userDataPath);
}

export function stopBackend(): Promise<void> {
  return new Promise((resolve) => {
    if (!backendProcess) {
      resolve();
      return;
    }

    console.log('[backend] Stopping...');

    const timeout = setTimeout(() => {
      console.log('[backend] Force killing...');
      try {
        if (backendProcess && !backendProcess.killed) {
          backendProcess.kill('SIGKILL');
        }
      } catch {
        // ignore
      }
      backendProcess = null;
      resolve();
    }, 5000);

    backendProcess.on('exit', () => {
      clearTimeout(timeout);
      backendProcess = null;
      console.log('[backend] Stopped.');
      resolve();
    });

    try {
      backendProcess.kill('SIGTERM');
    } catch {
      clearTimeout(timeout);
      backendProcess = null;
      resolve();
    }
  });
}
