import { NavLink } from 'react-router-dom';
import { PanelLeftClose, PanelLeftOpen, X } from 'lucide-react';
import { IconButton } from '../../shared/ui';
import { useAuth } from '../../shared/providers/AuthContext';
import { BrandMark } from './BrandMark';
import { DatasetSwitcher } from './DatasetSwitcher';
import { ModelStatus } from './ModelStatus';
import { UserMenu } from './UserMenu';
import { isNavItemAllowed, MAIN_NAV, PRODUCT_NAME, SETTINGS_NAV } from './nav';
import type { NavItem } from './nav';

interface SidebarProps {
  collapsed: boolean;
  onToggleCollapsed: () => void;
  /** Мобильная выезжающая панель */
  drawer?: boolean;
  onClose?: () => void;
}

export function Sidebar({ collapsed, onToggleCollapsed, drawer = false, onClose }: SidebarProps) {
  const { isAdmin, hasPermission } = useAuth();
  const compact = collapsed && !drawer;

  const visible = (item: NavItem) => isNavItemAllowed(item, isAdmin, hasPermission);
  const main = MAIN_NAV.filter(visible);
  const settings = SETTINGS_NAV.filter(visible);

  const renderLink = (item: NavItem) => {
    const Icon = item.icon;
    return (
      <li key={item.path}>
        <NavLink
          to={item.path}
          onClick={drawer ? onClose : undefined}
          className={({ isActive }) => `sidebar__link ${isActive ? 'is-active' : ''}`}
          data-tooltip-id={compact ? 'ui-tooltip' : undefined}
          data-tooltip-content={compact ? item.label : undefined}
          data-tooltip-place="right"
          aria-label={compact ? item.label : undefined}
        >
          <Icon className="sidebar__icon" aria-hidden="true" />
          {!compact && <span className="sidebar__label">{item.label}</span>}
        </NavLink>
      </li>
    );
  };

  return (
    <div className={`sidebar ${compact ? 'sidebar--compact' : ''}`}>
      <div className="sidebar__brand">
        <NavLink to="/" end className="sidebar__brand-link" aria-label={PRODUCT_NAME} onClick={drawer ? onClose : undefined}>
          <BrandMark />
          {!compact && <span className="sidebar__brand-name">{PRODUCT_NAME}</span>}
        </NavLink>
        {drawer && <IconButton label="Закрыть меню" icon={<X />} onClick={onClose} tooltip={false} />}
      </div>

      <div className="sidebar__switcher">
        <DatasetSwitcher compact={compact} onNavigate={drawer ? onClose : undefined} />
      </div>

      <nav className="sidebar__nav" aria-label="Основная навигация">
        <ul className="sidebar__list">{main.map(renderLink)}</ul>

        {settings.length > 0 && (
          <div className="sidebar__group">
            {compact ? <div className="sidebar__divider" role="separator" /> : <p className="sidebar__group-label">Настройки</p>}
            <ul className="sidebar__list">{settings.map(renderLink)}</ul>
          </div>
        )}
      </nav>

      <div className="sidebar__footer">
        <ModelStatus compact={compact} className="sidebar__model" />
        <div className="sidebar__footer-row">
          <UserMenu compact={compact} />
          {!drawer && (
            <IconButton
              label={collapsed ? 'Развернуть панель' : 'Свернуть панель'}
              icon={collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
              onClick={onToggleCollapsed}
              tooltipPlace="right"
              className="sidebar__collapse"
            />
          )}
        </div>
      </div>
    </div>
  );
}
