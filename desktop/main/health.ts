import http from 'http';

export interface HealthCheckOptions {
  url: string;
  intervalMs?: number;
  maxAttempts?: number;
  timeoutMs?: number;
}

export function pollHealth(options: HealthCheckOptions): Promise<boolean> {
  const {
    url,
    intervalMs = 500,
    maxAttempts = 60,
    timeoutMs = 2000,
  } = options;

  return new Promise((resolve) => {
    let attempts = 0;

    const check = () => {
      attempts++;
      // Одна попытка — один исход. destroy() при таймауте вызывает ещё и 'error', и раньше
      // обе ветки планировали повтор: цепочки удваивались и исчерпывали лимит попыток раньше
      // времени — медленный, но живой бэкенд (Rosetta) объявлялся упавшим
      let settled = false;
      const retryOrFail = () => {
        if (settled) return;
        settled = true;
        if (attempts < maxAttempts) {
          setTimeout(check, intervalMs);
        } else {
          resolve(false);
        }
      };
      const req = http.get(url, { timeout: timeoutMs }, (res) => {
        res.resume();
        if (res.statusCode === 200) {
          settled = true;
          resolve(true);
        } else {
          retryOrFail();
        }
      });

      req.on('error', retryOrFail);

      req.on('timeout', () => {
        req.destroy();
        retryOrFail();
      });
    };

    check();
  });
}
