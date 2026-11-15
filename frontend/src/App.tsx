import { BrowserRouter, HashRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Suspense, lazy } from 'react';
import type { ReactNode } from 'react';
import { Toaster } from 'react-hot-toast';
import { Tooltip } from 'react-tooltip';
import 'react-tooltip/dist/react-tooltip.css';
import ProtectedRoute, { HomeRedirect } from './components/ProtectedRoute';
import { AppShell } from './components/shell/AppShell';
import { MAIN_NAV, SETTINGS_NAV } from './components/shell/nav';
import { useAuth } from './shared/providers/AuthContext';
import { DatasetProvider } from './shared/providers/DatasetContext';
import { ConfirmProvider } from './shared/providers/ConfirmContext';
import { Spinner } from './shared/ui';
import { isFileProtocol } from './shared/lib/navigation';
import './App.css';

const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const AnalystPage = lazy(() => import('./pages/AnalystPage'));
const DetailsPage = lazy(() => import('./pages/DetailsPage'));
const UploadPage = lazy(() => import('./pages/UploadPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const StatusPage = lazy(() => import('./pages/StatusPage'));
const UsersPage = lazy(() => import('./pages/UsersPage'));
const LoginPage = lazy(() => import('./pages/LoginPage'));
const SessionsPage = lazy(() => import('./pages/SessionsPage'));
const CleanupPage = lazy(() => import('./pages/CleanupPage'));
const FeedbackStatsPage = lazy(() => import('./pages/FeedbackStatsPage'));
const ColumnDictPage = lazy(() => import('./pages/ColumnDictPage'));

/** Страница каждого раздела меню; права раздела — в shell/nav.ts */
const PAGES: Record<string, ReactNode> = {
  '/dashboard': <DashboardPage />,
  '/analyst': <AnalystPage />,
  '/sessions': <SessionsPage />,
  '/details': <DetailsPage />,
  '/settings/data': <UploadPage />,
  '/settings/column-dictionary': <ColumnDictPage />,
  '/settings/assistant': <SettingsPage />,
  '/settings/feedback': <FeedbackStatsPage />,
  '/settings/status': <StatusPage />,
  '/settings/cleanup': <CleanupPage />,
  '/settings/users': <UsersPage />,
};

function PageLoader() {
  return (
    <div className="page-loader" role="status" aria-label="Загрузка страницы">
      <Spinner size={16} />
    </div>
  );
}

function AuthenticatedRoutes() {
  return (
    <AppShell>
      <Suspense fallback={<PageLoader />}>
        <Routes>
          <Route element={<ProtectedRoute />}>
            {/* Права раздела берутся из его пункта меню (shell/nav.ts): скрытый раздел не открывается
                и по прямой ссылке, а правило прав записано в одном месте */}
            {[...MAIN_NAV, ...SETTINGS_NAV].map((item) => (
              <Route key={item.path} element={<ProtectedRoute requiredPermission={item.permission} requireAdmin={item.adminOnly} />}>
                <Route path={item.path} element={PAGES[item.path]} />
              </Route>
            ))}
            <Route path="/settings" element={<Navigate to="/settings/status" replace />} />
            <Route path="/" element={<HomeRedirect />} />
            <Route path="*" element={<HomeRedirect />} />
          </Route>
        </Routes>
      </Suspense>
    </AppShell>
  );
}

function AppContent() {
  const { isAuthenticated } = useAuth();

  if (!isAuthenticated) {
    return (
      <Suspense fallback={<PageLoader />}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
      </Suspense>
    );
  }

  return (
    <Routes>
      <Route path="/login" element={<HomeRedirect />} />
      <Route path="*" element={<AuthenticatedRoutes />} />
    </Routes>
  );
}

// В desktop-приложении страница открыта из файла: пути в hash, иначе перезагрузка ведёт на file:///dashboard
const Router = isFileProtocol ? HashRouter : BrowserRouter;

function App() {
  return (
    <DatasetProvider>
      <ConfirmProvider>
        <Toaster
          position="bottom-right"
          gutter={8}
          toastOptions={{
            className: 'ui-toast',
            style: {
              background: 'var(--surface-card)',
              color: 'var(--text-primary)',
              border: '1px solid var(--hairline-strong)',
              borderRadius: '10px',
              boxShadow: 'var(--shadow-float)',
              fontSize: '14px',
              padding: '8px 12px',
            },
            success: { iconTheme: { primary: 'var(--positive)', secondary: 'var(--surface-card)' } },
            error: { iconTheme: { primary: 'var(--negative)', secondary: 'var(--surface-card)' } },
          }}
        />
        <Router>
          <AppContent />
        </Router>
        <Tooltip id="ui-tooltip" className="ui-tooltip" delayShow={250} positionStrategy="fixed" />
      </ConfirmProvider>
    </DatasetProvider>
  );
}

export default App;
