import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { exec } from 'child_process'
import http from 'http'
import path from 'path'
import type { Plugin } from 'vite'

// Порт бэкенда: BACKEND_PORT из окружения (по умолчанию 8000) — один и тот же для прокси,
// проверки состояния и перезапуска. Только цифры: значение попадает в команды оболочки
const BACKEND_PORT = /^\d+$/.test(process.env.BACKEND_PORT ?? '') ? process.env.BACKEND_PORT! : '8000'

function serviceManagerPlugin(backendPort: string): Plugin {
  let healthCache: { backend: boolean; ollama: boolean; ts: number } | null = null
  const HEALTH_TTL_MS = 30_000

  return {
    name: 'service-manager',
    configureServer(server) {
      server.middlewares.use('/__internal/services/status', (_req, res) => {
        const now = Date.now()
        if (healthCache && now - healthCache.ts < HEALTH_TTL_MS) {
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ backend: healthCache.backend, ollama: healthCache.ollama }))
          return
        }
        Promise.all([
          new Promise<boolean>(resolve => {
            const req = http.get(`http://127.0.0.1:${backendPort}/openapi.json`, { timeout: 1000 }, (response) => {
              resolve(response.statusCode === 200)
              response.resume()
            }).on('error', () => resolve(false)).on('timeout', () => {
              req.destroy()
              resolve(false)
            })
          }),
          new Promise<boolean>(resolve => exec('pgrep -f "ollama serve"', (_err, stdout) => resolve(!!stdout.trim())))
        ]).then(([backendUp, ollamaUp]) => {
          healthCache = { backend: backendUp, ollama: ollamaUp, ts: now }
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({
            backend: backendUp,
            ollama: ollamaUp
          }))
        })
      })

      server.middlewares.use('/__internal/services/restart', (req, res) => {
        if (req.method !== 'POST') {
          res.statusCode = 405
          return res.end()
        }
        // Простой POST без заголовков CORS не останавливает: любой открытый сайт мог перезапускать
        // бэкенд и все `ollama serve` на машине. Свой заголовок требует preflight, на который
        // dev-сервер не отвечает разрешением, а Origin (если есть) должен быть самим dev-сервером
        const origin = req.headers.origin
        const host = req.headers.host || ''
        const sameOrigin = !origin || origin === `http://${host}` || origin === `https://${host}`
        if (req.headers['x-osa-dev'] !== '1' || !sameOrigin) {
          res.statusCode = 403
          return res.end()
        }
        try {
          const url = new URL(req.url || '', 'http://localhost')
          const service = url.searchParams.get('service')
          
          if (service === 'backend') {
              // Останавливаем только бэкенд на своём порту, а не любой uvicorn app.main:app на машине
              exec(`pkill -f "uvicorn app.main:app.*--port ${backendPort}( |$)"`, () => {
                setTimeout(() => {
                  const rootDir = path.resolve(process.cwd(), '..')
                  // Как в start.sh: только 127.0.0.1 (API и вход не видны из локальной сети) и python из venv
                  // напрямую — exec идёт через /bin/sh, где `source` есть не везде (в Linux это dash)
                  const cmd = `./venv/bin/python -m uvicorn app.main:app --reload --reload-dir app --host 127.0.0.1 --port ${backendPort} > logs/uvicorn_restart.log 2>&1 &`
                  exec(cmd, { cwd: rootDir })
                }, 1000);
              });
              res.setHeader('Content-Type', 'application/json')
              res.end(JSON.stringify({ success: true }))
            } else if (service === 'ollama') {
              exec('pkill -f "ollama serve"', () => {
                setTimeout(() => {
                  const logsDir = path.resolve(process.cwd(), '..', 'logs')
                  exec(`ollama serve > "${path.join(logsDir, 'ollama_restart.log')}" 2>&1 &`)
                }, 1000);
              });
              res.setHeader('Content-Type', 'application/json')
              res.end(JSON.stringify({ success: true }))
            } else {
              res.statusCode = 400
              res.end(JSON.stringify({ error: 'Unknown service' }))
            }
          } catch (e) {
            res.statusCode = 400
            res.end()
          }
      })
    }
  }
}

// https://vitejs.dev/config/
export default defineConfig({
  base: './',
  plugins: [react(), serviceManagerPlugin(BACKEND_PORT)],
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api/v1': {
        target: `http://127.0.0.1:${BACKEND_PORT}`,
        changeOrigin: true,
      },
      '/ws': {
        target: `ws://127.0.0.1:${BACKEND_PORT}`,
        ws: true,
        changeOrigin: true,
      }
    }
  }
})
