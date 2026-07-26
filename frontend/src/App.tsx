import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import Sidebar from './components/Sidebar';
import Header from './components/Header';
import ProtectedRoute from './components/ProtectedRoute';
import { useAuth } from './contexts/AuthContext';
import { DatasetProvider } from './context/DatasetContext';
import { Toaster } from 'react-hot-toast';
import { ConfirmProvider } from './context/ConfirmContext';
import { Suspense, lazy, useEffect, useState } from 'react';
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

function PageLoader() {
  return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-muted)' }}>Загрузка...</div>;
}

function AppContent() {
  const { isAuthenticated } = useAuth();
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  
  useEffect(() => {
    const savedTheme = localStorage.getItem('theme') || 'dark';
    document.documentElement.setAttribute('data-theme', savedTheme);
  }, []);
  
  return (
    <div className="app-layout">
      {isAuthenticated && <Header isSidebarCollapsed={isSidebarCollapsed} setIsSidebarCollapsed={setIsSidebarCollapsed} />}
      <div className="app">
        {isAuthenticated && <Sidebar isCollapsed={isSidebarCollapsed} />}
        <main className="app__main">
          <Suspense fallback={<PageLoader />}>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              
              <Route element={<ProtectedRoute />}>
                <Route path="/dashboard" element={<DashboardPage />} />
                <Route path="/analyst" element={<AnalystPage />} />
                <Route path="/sessions" element={<SessionsPage />} />
                <Route path="/details" element={<DetailsPage />} />
                <Route path="/settings" element={<Navigate to="/settings/status" replace />} />
                <Route element={<ProtectedRoute requiredPermission="settings_status" />}>
                  <Route path="/settings/status" element={<StatusPage />} />
                </Route>
                <Route element={<ProtectedRoute requiredPermission="settings_assistant" />}>
                  <Route path="/settings/assistant" element={<SettingsPage />} />
                </Route>
                <Route element={<ProtectedRoute requiredPermission="settings_data" />}>
                  <Route path="/settings/data" element={<UploadPage />} />
                </Route>
                <Route element={<ProtectedRoute requiredPermission="settings_cleanup" />}>
                  <Route path="/settings/cleanup" element={<CleanupPage />} />
                </Route>
                <Route element={<ProtectedRoute requiredPermission="settings_assistant" />}>
                  <Route path="/settings/feedback" element={<FeedbackStatsPage />} />
                </Route>
                <Route element={<ProtectedRoute requiredPermission="settings_assistant" />}>
                  <Route path="/settings/column-dictionary" element={<ColumnDictPage />} />
                </Route>
                <Route path="/" element={<Navigate to="/dashboard" replace />} />
              </Route>
              
              <Route element={<ProtectedRoute requireAdmin={true} />}>
                <Route path="/settings/users" element={<UsersPage />} />
              </Route>
              
              <Route path="*" element={<Navigate to={isAuthenticated ? "/dashboard" : "/login"} replace />} />
            </Routes>
          </Suspense>
        </main>
      </div>
    </div>
  );
}

function App() {
  return (
    <DatasetProvider>
      <ConfirmProvider>
        <Toaster position="bottom-right" toastOptions={{
          style: {
            background: 'var(--bg-secondary)',
            color: 'var(--text-primary)',
            border: '1px solid var(--border-light)',
          }
        }} />
        <Router>
          <AppContent />
        </Router>
      </ConfirmProvider>
    </DatasetProvider>
  );
}

export default App;
