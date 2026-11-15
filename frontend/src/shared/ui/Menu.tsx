import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { ReactNode, Ref } from 'react';
import { Check } from 'lucide-react';
import './Menu.css';

export interface MenuItem {
  type?: 'item';
  key?: string;
  label: ReactNode;
  icon?: ReactNode;
  meta?: ReactNode;
  onSelect?: () => void;
  tone?: 'default' | 'danger';
  disabled?: boolean;
  selected?: boolean;
}

export type MenuEntry =
  | MenuItem
  | { type: 'separator'; key?: string }
  | { type: 'label'; key?: string; label: ReactNode };

export interface MenuTriggerProps {
  ref: Ref<HTMLButtonElement>;
  onClick: () => void;
  'aria-haspopup': 'menu';
  'aria-expanded': boolean;
  'aria-controls': string;
}

interface MenuProps {
  trigger: (props: MenuTriggerProps, open: boolean) => ReactNode;
  items: MenuEntry[];
  align?: 'start' | 'end' | 'stretch';
  side?: 'bottom' | 'top';
  width?: number | string;
  header?: ReactNode;
  className?: string;
}

export function Menu({ trigger, items, align = 'start', side = 'bottom', width, header, className = '' }: MenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  const close = useCallback((returnFocus = true) => {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) close(false);
    };
    document.addEventListener('mousedown', onPointer);
    const first = listRef.current?.querySelector<HTMLElement>('[role="menuitem"]:not([aria-disabled="true"])');
    first?.focus();
    return () => document.removeEventListener('mousedown', onPointer);
  }, [open, close]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open) return;
    const nodes = Array.from(listRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([aria-disabled="true"])') ?? []);
    const index = nodes.indexOf(document.activeElement as HTMLElement);
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      close();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      nodes[(index + 1) % nodes.length]?.focus();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      nodes[(index - 1 + nodes.length) % nodes.length]?.focus();
    } else if (e.key === 'Home') {
      e.preventDefault();
      nodes[0]?.focus();
    } else if (e.key === 'End') {
      e.preventDefault();
      nodes[nodes.length - 1]?.focus();
    } else if (e.key === 'Tab') {
      close(false);
    }
  };

  return (
    <div ref={rootRef} className={`ui-menu-root ${align === 'stretch' ? 'ui-menu-root--stretch' : ''} ${className}`} onKeyDown={onKeyDown}>
      {trigger(
        {
          ref: triggerRef,
          onClick: () => setOpen((o) => !o),
          'aria-haspopup': 'menu',
          'aria-expanded': open,
          'aria-controls': menuId,
        },
        open,
      )}
      {open && (
        <div
          ref={listRef}
          id={menuId}
          role="menu"
          className={`ui-menu ui-menu--${side} ui-menu--${align}`}
          style={width ? { width } : undefined}
        >
          {header && <div className="ui-menu__header">{header}</div>}
          {items.map((entry, i) => {
            const key = entry.key ?? String(i);
            if (entry.type === 'separator') return <div key={key} className="ui-menu__separator" role="separator" />;
            if (entry.type === 'label') return <div key={key} className="ui-menu__label">{entry.label}</div>;
            const item = entry as MenuItem;
            return (
              <button
                key={key}
                type="button"
                role="menuitem"
                tabIndex={-1}
                aria-disabled={item.disabled || undefined}
                className={`ui-menu__item ${item.tone === 'danger' ? 'ui-menu__item--danger' : ''} ${item.selected ? 'is-selected' : ''}`}
                onClick={() => {
                  if (item.disabled) return;
                  close();
                  item.onSelect?.();
                }}
              >
                {item.icon && <span className="ui-menu__icon">{item.icon}</span>}
                <span className="ui-menu__text">{item.label}</span>
                {item.meta && <span className="ui-menu__meta">{item.meta}</span>}
                {item.selected && <Check className="ui-menu__check" aria-hidden="true" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
