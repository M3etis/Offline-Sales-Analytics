import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { AuthProvider } from './shared/providers/AuthContext'
import { applyStoredTheme } from './shared/lib/theme'
import './index.css'

// До первой отрисовки: из useEffect тема применялась после первого кадра, и светлая тема
// при каждой загрузке на мгновение показывалась тёмной (тёмная — значение по умолчанию в tokens.css)
applyStoredTheme()

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <AuthProvider>
      <App />
    </AuthProvider>
  </React.StrictMode>,
)
