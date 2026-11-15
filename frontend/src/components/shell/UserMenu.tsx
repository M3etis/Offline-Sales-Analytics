import { useNavigate } from 'react-router-dom';
import { LogOut, Moon, Sun } from 'lucide-react';
import { Menu } from '../../shared/ui';
import { useAuth } from '../../shared/providers/AuthContext';
import { useTheme } from '../../shared/lib/theme';

export function UserMenu({ compact = false }: { compact?: boolean }) {
  const { username, fullName, role, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();

  const displayName = fullName || username || 'Пользователь';
  const initial = displayName.trim()[0]?.toUpperCase() ?? '?';
  const roleLabel = role === 'admin' ? 'Администратор' : 'Пользователь';

  return (
    <Menu
      side="top"
      align="start"
      width={232}
      className="user-menu"
      header={
        <div className="user-menu__header">
          <span className="user-menu__name">{displayName}</span>
          <span className="user-menu__login">
            {username} · {roleLabel}
          </span>
        </div>
      }
      items={[
        {
          key: 'theme',
          label: theme === 'dark' ? 'Светлая тема' : 'Тёмная тема',
          icon: theme === 'dark' ? <Sun /> : <Moon />,
          onSelect: toggleTheme,
        },
        { type: 'separator' },
        {
          key: 'logout',
          label: 'Выйти',
          icon: <LogOut />,
          onSelect: () => {
            logout();
            navigate('/login');
          },
        },
      ]}
      trigger={(props) => (
        <button
          {...props}
          type="button"
          className={`user-menu__trigger ${compact ? 'user-menu__trigger--compact' : ''}`}
          aria-label={`Аккаунт: ${displayName}`}
          data-tooltip-id={compact ? 'ui-tooltip' : undefined}
          data-tooltip-content={compact ? displayName : undefined}
          data-tooltip-place="right"
        >
          <span className="user-menu__avatar" aria-hidden="true">
            {initial}
          </span>
          {!compact && (
            <span className="user-menu__text">
              <span className="user-menu__trigger-name">{displayName}</span>
              <span className="user-menu__trigger-role">{roleLabel === displayName ? username : roleLabel}</span>
            </span>
          )}
        </button>
      )}
    />
  );
}
