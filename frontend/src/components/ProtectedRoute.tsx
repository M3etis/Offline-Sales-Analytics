import React from 'react';
import { Navigate, Outlet } from 'react-router-dom';
import { Lock } from 'lucide-react';
import { useAuth } from '../shared/providers/AuthContext';
import { firstAllowedPath } from './shell/nav';
import { EmptyState, Page, PageHeader, Panel } from '../shared/ui';

interface ProtectedRouteProps {
  requireAdmin?: boolean;
  requiredPermission?: string;
}

export const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ requireAdmin = false, requiredPermission }) => {
  const { isAuthenticated, isAdmin, hasPermission } = useAuth();

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  if ((requireAdmin && !isAdmin) || (requiredPermission && !hasPermission(requiredPermission))) {
    return <HomeRedirect />;
  }

  return <Outlet />;
};

/** Переход в первый доступный пользователю раздел. Если доступных нет — сообщение, а не цикл редиректов */
export function HomeRedirect() {
  const { isAdmin, hasPermission } = useAuth();
  const home = firstAllowedPath(isAdmin, hasPermission);
  if (!home) {
    return (
      <Page width="narrow">
        <PageHeader title="Нет доступа" />
        <Panel>
          <EmptyState icon={<Lock />} title="Нет доступных разделов" description="Попросите администратора выдать доступ в разделе «Пользователи»." />
        </Panel>
      </Page>
    );
  }
  return <Navigate to={home} replace />;
}

export default ProtectedRoute;
