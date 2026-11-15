import { useEffect, useState } from 'react';
import { aiApi } from '../api/ai.api';

export type ModelState = 'checking' | 'ready' | 'missing' | 'offline';

export interface ModelStatus {
  state: ModelState;
  model: string | null;
  detail: string | null;
  /** Ответил ли сервер приложения на последний опрос; null — ответа ещё не было */
  serverReachable: boolean | null;
}

const POLL_MS = 30_000;
/** Такие коды отдаёт прокси, когда сам сервер приложения не отвечает */
const GATEWAY_ERRORS = [502, 503, 504];

let current: ModelStatus = { state: 'checking', model: null, detail: null, serverReachable: null };
const subscribers = new Set<(s: ModelStatus) => void>();
let timer: ReturnType<typeof setInterval> | null = null;
/** Идущая проверка: опрос по таймеру присоединяется к ней, а не шлёт второй запрос */
let inFlight: Promise<void> | null = null;

function check(): Promise<void> {
  if (!inFlight) {
    inFlight = runCheck().finally(() => {
      inFlight = null;
    });
  }
  return inFlight;
}

async function runCheck() {
  try {
    const res = await aiApi.getModelStatus();
    if (res.status === 'ok') {
      current = {
        state: res.model_available ? 'ready' : 'missing',
        model: res.model ?? null,
        detail: res.model_available ? null : `Модель ${res.model ?? ''} не загружена в Ollama`,
        serverReachable: true,
      };
    } else {
      current = { state: 'offline', model: res.model ?? current.model, detail: res.error ?? 'Ollama не запущена', serverReachable: true };
    }
  } catch (e) {
    const httpStatus = (e as { response?: { status?: number } })?.response?.status;
    // 401 — истёк вход, а не сбой: статус не меняем, выход случится на следующем действии пользователя
    if (httpStatus === 401) return;
    const serverReachable = httpStatus !== undefined && !GATEWAY_ERRORS.includes(httpStatus);
    current = { state: 'offline', model: current.model, detail: 'Сервер недоступен', serverReachable };
  } finally {
    subscribers.forEach((fn) => fn(current));
  }
}

/**
 * Перепроверить статус сразу, не дожидаясь очередного опроса (например, после смены модели).
 * Если проверка уже идёт, она могла начаться до смены и вернуть устаревший статус —
 * поэтому дожидаемся её и проверяем ещё раз.
 */
export function refreshModelStatus(): Promise<void> {
  return inFlight ? inFlight.then(() => check()) : check();
}

// Свёрнутое или скрытое окно не опрашиваем: статус никто не видит. При возврате — сразу проверяем
function tick() {
  if (document.visibilityState === 'hidden') return;
  check();
}

function onVisibilityChange() {
  if (document.visibilityState === 'visible') check();
}

/** Живой статус локальной модели. Один опрос на всё приложение. */
export function useModelStatus(): ModelStatus {
  const [status, setStatus] = useState<ModelStatus>(current);

  useEffect(() => {
    subscribers.add(setStatus);
    if (subscribers.size === 1) {
      check();
      timer = setInterval(tick, POLL_MS);
      document.addEventListener('visibilitychange', onVisibilityChange);
    } else {
      setStatus(current);
    }
    return () => {
      subscribers.delete(setStatus);
      if (subscribers.size === 0 && timer) {
        clearInterval(timer);
        timer = null;
        document.removeEventListener('visibilitychange', onVisibilityChange);
      }
    };
  }, []);

  return status;
}
