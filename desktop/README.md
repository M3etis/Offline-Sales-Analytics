# Sales Analytics — Desktop (Electron)

Desktop-обёртка для Sales Analytics на Electron.

## Быстрый старт (Dev)

```bash
cd desktop
npm install
npm run dev
```

Запустит:
- Frontend dev server (порт 5173)
- Backend FastAPI (порт 8000)
- Electron окно

## Сборка (Production)

```bash
cd desktop
npm run package
```

Собирает:
1. Frontend → `../frontend/dist/`
2. Backend binary → `desktop/build/backend/`
3. Electron app → `desktop/release/`

## Структура

```
desktop/
├── main/              # Electron main process
│   ├── index.ts       # Точка входа
│   ├── preload.ts     # Preload script (contextBridge)
│   ├── backend.ts     # Менеджер backend процесса
│   ├── ollama.ts      # Проверка доступности Ollama
│   └── health.ts      # Health check polling
├── scripts/           # Скрипты сборки и запуска
├── resources/         # Иконки приложения
├── package.json       # Зависимости Electron
├── tsconfig.json      # TypeScript конфиг
└── electron-builder.yml  # Конфиг упаковки
```

## Зависимости

- Node.js 18+
- Python 3.10+ (с venv в корне проекта)
- Ollama (опционально, для AI-функций)

## Безопасность

- `contextIsolation: true`
- `nodeIntegration: false`
- `sandbox: true`
- Preload script exposes only `electronAPI.platform` and `electronAPI.isDesktop`
