import { ChildProcess, spawn, execSync } from 'child_process';
import path from 'path';
import os from 'os';
import fs from 'fs';
import { pollHealth } from './health';

let backendProcess: ChildProcess | null = null;

function getLogFile(resourcesPath: string, name: string): fs.WriteStream {
  const logsDir = path.join(resourcesPath, 'logs');
  fs.mkdirSync(logsDir, { recursive: true });
  return fs.createWriteStream(path.join(logsDir, name), { flags: 'a' });
}

export interface BackendStartOptions {
  isDev: boolean;
  projectRoot: string;
  resourcesPath: string;
}

function log(prefix: string, data: Buffer) {
  const lines = data.toString().trim().split('\n');
  for (const line of lines) {
    console.log(`[backend] ${line}`);
  }
}

export async function startBackend(options: BackendStartOptions): Promise<boolean> {
  const { isDev, projectRoot, resourcesPath } = options;

  if (isDev) {
    return startBackendDev(projectRoot);
  } else {
    return startBackendProd(resourcesPath);
  }
}

function startBackendDev(projectRoot: string): Promise<boolean> {
  const venvPython = process.platform === 'win32'
    ? path.join(projectRoot, 'venv', 'Scripts', 'python.exe')
    : path.join(projectRoot, 'venv', 'bin', 'python');

  console.log('[backend] Starting FastAPI in dev mode...');

  backendProcess = spawn(venvPython, [
    '-m', 'uvicorn',
    'app.main:app',
    '--host', '127.0.0.1',
    '--port', '8000',
  ], {
    cwd: projectRoot,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      SECRET_KEY: process.env.SECRET_KEY || 'b02dbcb8057ddf1109fb33c47c7b9d17bb377e5e49de92b9914033017e662214',
    },
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
    url: 'http://127.0.0.1:8000/health',
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

function findPython(): string | null {
  if (process.platform === 'win32') {
    const candidates = ['python', 'python3', 'py'];
    for (const cmd of candidates) {
      try {
        const result = execSync(`where ${cmd}`, { encoding: 'utf8', timeout: 3000 }).trim();
        if (result) return result.split('\n')[0].trim();
      } catch { /* not found */ }
    }
  } else {
    const candidates = [
      '/opt/homebrew/bin/python3',
      '/usr/local/bin/python3',
      '/usr/bin/python3',
      'python3',
    ];
    for (const cmd of candidates) {
      try {
        execSync(`"${cmd}" --version`, { encoding: 'utf8', timeout: 3000 });
        return cmd;
      } catch { /* not found */ }
    }
  }
  return null;
}

function startBackendWithBinary(binaryPath: string, resourcesPath: string): Promise<boolean> {
  const backendDir = path.dirname(binaryPath);
  const dataDir = path.join(resourcesPath, 'data');
  const logStream = getLogFile(resourcesPath, 'backend.log');

  console.log(`[backend] Starting binary: ${binaryPath}`);

  backendProcess = spawn(binaryPath, [], {
    cwd: backendDir,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      DB_PATH: path.join(dataDir, 'processed', 'sales.duckdb'),
      DATA_DIR: dataDir,
      LOG_DIR: path.join(resourcesPath, 'logs'),
      SECRET_KEY: process.env.SECRET_KEY || 'b02dbcb8057ddf1109fb33c47c7b9d17bb377e5e49de92b9914033017e662214',
    },
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
    url: 'http://127.0.0.1:8000/health',
    intervalMs: 500,
    maxAttempts: 30,
  });
}

function startBackendWithPython(pythonPath: string, resourcesPath: string): Promise<boolean> {
  const appDir = path.join(resourcesPath, 'app');
  const dataDir = path.join(resourcesPath, 'data');
  const logStream = getLogFile(resourcesPath, 'backend.log');

  console.log(`[backend] Falling back to Python: ${pythonPath}`);
  console.log(`[backend] App dir: ${appDir}`);

  backendProcess = spawn(pythonPath, [
    '-m', 'uvicorn',
    'app.main:app',
    '--host', '127.0.0.1',
    '--port', '8000',
  ], {
    cwd: resourcesPath,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      PYTHONPATH: resourcesPath,
      DB_PATH: path.join(dataDir, 'processed', 'sales.duckdb'),
      DATA_DIR: dataDir,
      LOG_DIR: path.join(resourcesPath, 'logs'),
      SECRET_KEY: process.env.SECRET_KEY || 'b02dbcb8057ddf1109fb33c47c7b9d17bb377e5e49de92b9914033017e662214',
    },
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
    url: 'http://127.0.0.1:8000/health',
    intervalMs: 500,
    maxAttempts: 60,
  });
}

async function startBackendProd(resourcesPath: string): Promise<boolean> {
  const binaryPath = getBackendBinary(resourcesPath);
  const dataDir = path.join(resourcesPath, 'data');

  console.log(`[backend] Architecture: ${os.arch()}`);
  console.log(`[backend] Data dir: ${dataDir}`);

  // Try native binary first
  if (fs.existsSync(binaryPath)) {
    console.log(`[backend] Trying native binary...`);
    const ready = await startBackendWithBinary(binaryPath, resourcesPath);

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
        url: 'http://127.0.0.1:8000/health',
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
    console.error('[backend] No Python found! Cannot start backend.');
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

  return startBackendWithPython(python, resourcesPath);
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
