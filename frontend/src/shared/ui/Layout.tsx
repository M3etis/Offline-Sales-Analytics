import type { HTMLAttributes, ReactNode } from 'react';
import './Layout.css';

interface PageProps {
  children: ReactNode;
  width?: 'default' | 'narrow' | 'wide';
  className?: string;
}

/** Прокручиваемая область страницы с ограничением ширины */
export function Page({ children, width = 'default', className = '' }: PageProps) {
  return (
    <div className="ui-page">
      <div className={`ui-page__inner ui-page__inner--${width} ${className}`}>{children}</div>
    </div>
  );
}

interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
}

export function PageHeader({ title, description, actions, className = '' }: PageHeaderProps) {
  return (
    <header className={`ui-page-header ${className}`}>
      <div className="ui-page-header__text">
        <h1 className="ui-page-header__title">{title}</h1>
        {description && <p className="ui-page-header__description">{description}</p>}
      </div>
      {actions && <div className="ui-page-header__actions">{actions}</div>}
    </header>
  );
}

interface PanelProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  padding?: 'none' | 'md';
  tone?: 'default' | 'danger';
  children?: ReactNode;
}

export function Panel({ title, description, actions, padding = 'md', tone = 'default', className = '', children, ...rest }: PanelProps) {
  const hasHeader = title || description || actions;
  return (
    <section className={`ui-panel ui-panel--pad-${padding} ui-panel--${tone} ${className}`} {...rest}>
      {hasHeader && (
        <div className="ui-panel__header">
          <div className="ui-panel__heading">
            {title && <h2 className="ui-panel__title">{title}</h2>}
            {description && <p className="ui-panel__description">{description}</p>}
          </div>
          {actions && <div className="ui-panel__actions">{actions}</div>}
        </div>
      )}
      {children !== undefined && <div className="ui-panel__body">{children}</div>}
    </section>
  );
}

export function Toolbar({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`ui-toolbar ${className}`}>{children}</div>;
}

export function ToolbarSpacer() {
  return <div className="ui-toolbar__spacer" />;
}
