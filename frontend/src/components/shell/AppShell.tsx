import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { Menu as MenuIcon } from 'lucide-react';
import { IconButton } from '../../shared/ui';
import { Sidebar } from './Sidebar';
import { BrandMark } from './BrandMark';
import { findNavItem, PRODUCT_NAME } from './nav';
import './shell.css';

const COLLAPSE_KEY = 'sidebarCollapsed';

export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(COLLAPSE_KEY) === '1';
    } catch {
      return false;
    }
  });
  const [drawerOpen, setDrawerOpen] = useState(false);

  const current = findNavItem(location.pathname);

  useEffect(() => {
    document.title = current ? `${current.label} · ${PRODUCT_NAME}` : PRODUCT_NAME;
  }, [current]);

  // Закрыть мобильную панель при переходе
  useEffect(() => {
    setDrawerOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDrawerOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [drawerOpen]);

  const toggleCollapsed = () => {
    setCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem(COLLAPSE_KEY, next ? '1' : '0');
      } catch {
        /* ignore */
      }
      return next;
    });
  };

  return (
    <div className={`shell ${collapsed ? 'shell--collapsed' : ''} ${drawerOpen ? 'shell--drawer-open' : ''}`}>
      <aside className="shell__sidebar shell__sidebar--desktop" aria-label="Боковая панель">
        <Sidebar collapsed={collapsed} onToggleCollapsed={toggleCollapsed} />
      </aside>

      <div className="shell__drawer-layer" aria-hidden={!drawerOpen}>
        <div className="shell__drawer-overlay" onClick={() => setDrawerOpen(false)} />
        <aside className="shell__sidebar shell__sidebar--drawer" aria-label="Меню" inert={!drawerOpen}>
          <Sidebar collapsed={false} onToggleCollapsed={toggleCollapsed} drawer onClose={() => setDrawerOpen(false)} />
        </aside>
      </div>

      <div className="shell__main">
        <header className="shell__mobile-bar">
          <IconButton label="Открыть меню" icon={<MenuIcon />} onClick={() => setDrawerOpen(true)} tooltip={false} aria-expanded={drawerOpen} />
          <BrandMark size={18} />
          <span className="shell__mobile-title">{current?.label ?? PRODUCT_NAME}</span>
        </header>
        <main className="shell__content" id="main">
          {children}
        </main>
      </div>
    </div>
  );
}
