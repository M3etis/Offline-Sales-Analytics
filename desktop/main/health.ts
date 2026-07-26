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
      const req = http.get(url, { timeout: timeoutMs }, (res) => {
        res.resume();
        if (res.statusCode === 200) {
          resolve(true);
        } else if (attempts < maxAttempts) {
          setTimeout(check, intervalMs);
        } else {
          resolve(false);
        }
      });

      req.on('error', () => {
        if (attempts < maxAttempts) {
          setTimeout(check, intervalMs);
        } else {
          resolve(false);
        }
      });

      req.on('timeout', () => {
        req.destroy();
        if (attempts < maxAttempts) {
          setTimeout(check, intervalMs);
        } else {
          resolve(false);
        }
      });
    };

    check();
  });
}
