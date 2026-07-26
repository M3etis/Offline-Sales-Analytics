import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { exec } from 'child_process'
import http from 'http'
import path from 'path'
import type { Plugin } from 'vite'

function serviceManagerPlugin(): Plugin {
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
            const req = http.get('http://127.0.0.1:8000/openapi.json', { timeout: 1000 }, (response) => {
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
        try {
          const url = new URL(req.url || '', 'http://localhost')
          const service = url.searchParams.get('service')
          
          if (service === 'backend') {
              exec('pkill -f "uvicorn app.main:app"', () => {
                setTimeout(() => {
                  const rootDir = path.resolve(process.cwd(), '..')
                  const cmd = 'source venv/bin/activate && uvicorn app.main:app --reload --reload-dir app --host 0.0.0.0 --port 8000 > logs/uvicorn_restart.log 2>&1 &'
                  exec(cmd, { cwd: rootDir })
                }, 1000);
              });
              res.setHeader('Content-Type', 'application/json')
              res.end(JSON.stringify({ success: true }))
            } else if (service === 'ollama') {
              exec('pkill -f "ollama serve"', () => {
                setTimeout(() => {
                  const logsDir = path.resolve(process.cwd(), '..', 'logs')
                  exec(`ollama serve > ${logsDir}/ollama_restart.log 2>&1 &`)
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
  plugins: [react(), serviceManagerPlugin()],
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api/v1': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
        changeOrigin: true,
      }
    }
  }
})
