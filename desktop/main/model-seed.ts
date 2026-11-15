/**
 * Перенос встроенной в сборку модели в папку моделей пользователя.
 *
 * Внутри приложения писать нельзя: на Windows Program Files закрыт для записи, на macOS
 * изменения ломают подпись. Поэтому Ollama работает с папкой пользователя (~/.ollama/models),
 * а встроенная модель при запуске «прописывается» туда:
 *   - файлы весов — жёсткой ссылкой (мгновенно и без лишнего места, переживает удаление приложения);
 *   - если жёсткая ссылка невозможна (другой диск, macOS запускает приложение из временной копии) —
 *     символической ссылкой (macOS, Linux), а на Windows — копией;
 *   - манифест — копией, и только после того как все файлы на месте, чтобы Ollama не увидела
 *     модель с недостающими файлами.
 * Ссылки, ведущие в никуда (приложение переместили), чинятся при следующем запуске.
 */
import fs from 'fs';
import path from 'path';

interface Manifest {
  config: { digest: string };
  layers?: { digest: string }[];
}

type Placement = 'kept' | 'hardlink' | 'symlink' | 'copy';

const blobName = (digest: string) => digest.replace(':', '-');

function log(message: string) {
  console.log(`[models] ${message}`);
}

function listFiles(dir: string, base = dir): string[] {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return listFiles(full, base);
    return entry.isFile() ? [path.relative(base, full)] : [];
  });
}

function readManifest(file: string): Manifest | null {
  try {
    const manifest = JSON.parse(fs.readFileSync(file, 'utf8')) as Manifest;
    return manifest?.config?.digest ? manifest : null;
  } catch {
    return null;
  }
}

function digestsOf(manifest: Manifest): string[] {
  return [manifest.config.digest, ...(manifest.layers ?? []).map((layer) => layer.digest)];
}

/** Все файлы модели на месте (statSync идёт по ссылкам, поэтому «мёртвая» ссылка — это отсутствие файла) */
function blobsPresent(modelsDir: string, manifest: Manifest): boolean {
  return digestsOf(manifest).every((digest) => {
    try {
      return fs.statSync(path.join(modelsDir, 'blobs', blobName(digest))).isFile();
    } catch {
      return false;
    }
  });
}

function processAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === 'EPERM'; // есть, но чужой
  }
}

/** Недокопированные файлы прошлых запусков (приложение закрыли посреди копирования) занимают гигабайты */
function removeStaleTemp(dir: string) {
  if (!fs.existsSync(dir)) return;
  for (const name of fs.readdirSync(dir)) {
    const m = /\.seeding(?:-(\d+))?$/.exec(name);
    if (!m) continue;
    const pid = m[1] ? Number(m[1]) : null;
    if (pid !== null && pid !== process.pid && processAlive(pid)) continue; // копирует другой запуск
    fs.rmSync(path.join(dir, name), { force: true });
  }
}

async function placeBlob(src: string, dst: string): Promise<Placement> {
  const size = fs.statSync(src).size;

  let existing: fs.Stats | null = null;
  try {
    existing = fs.lstatSync(dst);
  } catch {
    /* файла нет */
  }
  if (existing) {
    try {
      // Обычный файл или ссылка на живой файл нужного размера — уже установлено
      if (fs.statSync(dst).size === size) return 'kept';
    } catch {
      /* ссылка в никуда */
    }
    fs.unlinkSync(dst);
  }

  try {
    fs.linkSync(src, dst);
    return 'hardlink';
  } catch {
    /* другой диск, временная копия приложения или нет прав */
  }

  if (process.platform !== 'win32') {
    try {
      fs.symlinkSync(src, dst);
      return 'symlink';
    } catch {
      /* падаем на копирование */
    }
  }

  // Имя временного файла своё у каждого процесса: два одновременных первых запуска
  // не пишут в один и тот же файл и не удаляют копию друг друга
  const tmp = `${dst}.seeding-${process.pid}`;
  try {
    // На APFS и других файловых системах с клонированием это мгновенная копия без расхода места
    await fs.promises.copyFile(src, tmp, fs.constants.COPYFILE_FICLONE);
    fs.renameSync(tmp, dst);
  } catch (err) {
    fs.rmSync(tmp, { force: true });
    throw err;
  }
  return 'copy';
}

/**
 * Прописать встроенные модели в modelsDir. Возвращает false, если перенести не удалось —
 * тогда вызывающий может запустить Ollama прямо со встроенной папкой, как раньше.
 */
export async function seedBundledModels(resourcesPath: string, modelsDir: string): Promise<boolean> {
  const bundleDir = path.join(resourcesPath, 'ollama-models');
  const manifests = listFiles(path.join(bundleDir, 'manifests'));
  if (manifests.length === 0) return true;

  try {
    fs.mkdirSync(path.join(modelsDir, 'blobs'), { recursive: true });
    removeStaleTemp(path.join(modelsDir, 'blobs'));

    for (const rel of manifests) {
      const manifest = readManifest(path.join(bundleDir, 'manifests', rel));
      if (!manifest) continue;

      const target = path.join(modelsDir, 'manifests', rel);
      const installed = readManifest(target);
      if (installed && blobsPresent(modelsDir, installed)) {
        // Модель уже есть у пользователя (возможно, более свежая версия) — не трогаем
        continue;
      }

      const started = Date.now();
      const placements = new Set<Placement>();
      for (const digest of digestsOf(manifest)) {
        const src = path.join(bundleDir, 'blobs', blobName(digest));
        placements.add(await placeBlob(src, path.join(modelsDir, 'blobs', blobName(digest))));
      }

      fs.mkdirSync(path.dirname(target), { recursive: true });
      const tmp = `${target}.seeding-${process.pid}`;
      fs.copyFileSync(path.join(bundleDir, 'manifests', rel), tmp);
      fs.renameSync(tmp, target);

      log(`${rel} → ${modelsDir} (${[...placements].join(', ')}, ${Date.now() - started} мс)`);
    }
    return true;
  } catch (err) {
    log(`Не удалось перенести встроенную модель в ${modelsDir}: ${err instanceof Error ? err.message : String(err)}`);
    return false;
  }
}
