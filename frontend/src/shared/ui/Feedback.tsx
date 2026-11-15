import type { CSSProperties, ReactNode } from 'react';
import { AlertCircle, CheckCircle2, Info, RotateCw, X } from 'lucide-react';
import { Button, IconButton } from './Button';
import './Feedback.css';

type Tone = 'neutral' | 'accent' | 'positive' | 'negative';

export function Badge({ tone = 'neutral', children, className = '', title }: { tone?: Tone; children: ReactNode; className?: string; title?: string }) {
  return (
    <span className={`ui-badge ui-badge--${tone} ${className}`} title={title}>
      {children}
    </span>
  );
}

export function StatusDot({ tone = 'neutral', label }: { tone?: Tone; label?: string }) {
  return <span className={`ui-dot ui-dot--${tone}`} role={label ? 'img' : undefined} aria-label={label} aria-hidden={label ? undefined : true} />;
}

interface EmptyStateProps {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
  className?: string;
}

export function EmptyState({ icon, title, description, action, compact = false, className = '' }: EmptyStateProps) {
  return (
    <div className={`ui-empty ${compact ? 'ui-empty--compact' : ''} ${className}`}>
      {icon && <div className="ui-empty__icon">{icon}</div>}
      <p className="ui-empty__title">{title}</p>
      {description && <p className="ui-empty__description">{description}</p>}
      {action && <div className="ui-empty__action">{action}</div>}
    </div>
  );
}

interface ErrorStateProps {
  title?: ReactNode;
  description?: ReactNode;
  onRetry?: () => void;
  compact?: boolean;
}

export function ErrorState({ title = 'Не удалось загрузить данные', description = 'Проверьте, что сервер запущен, и попробуйте ещё раз.', onRetry, compact = false }: ErrorStateProps) {
  return (
    <EmptyState
      compact={compact}
      className="ui-empty--error"
      icon={<AlertCircle />}
      title={title}
      description={description}
      action={
        onRetry ? (
          <Button size="sm" icon={<RotateCw />} onClick={onRetry}>
            Повторить
          </Button>
        ) : undefined
      }
    />
  );
}

export function Skeleton({ width = '100%', height = 16, radius = 6, className = '', style }: { width?: number | string; height?: number | string; radius?: number; className?: string; style?: CSSProperties }) {
  return <span className={`ui-skeleton ${className}`} style={{ width, height, borderRadius: radius, ...style }} aria-hidden="true" />;
}

/** Заглушка списка на время загрузки: строки заданной ширины. Отступы задаёт className страницы */
export function SkeletonLines({ widths, className = '' }: { widths: (number | string)[]; className?: string }) {
  return (
    <div className={`ui-skeleton-lines ${className}`} role="status" aria-busy="true" aria-label="Загрузка">
      {widths.map((width, i) => (
        <Skeleton key={i} height={14} width={width} />
      ))}
    </div>
  );
}

interface AlertProps {
  tone?: Tone;
  title?: ReactNode;
  children?: ReactNode;
  onClose?: () => void;
  action?: ReactNode;
  className?: string;
}

const ALERT_ICONS: Record<Tone, ReactNode> = {
  neutral: <Info />,
  accent: <Info />,
  positive: <CheckCircle2 />,
  negative: <AlertCircle />,
};

export function Alert({ tone = 'neutral', title, children, onClose, action, className = '' }: AlertProps) {
  return (
    <div className={`ui-alert ui-alert--${tone} ${className}`} role={tone === 'negative' ? 'alert' : 'status'}>
      <span className="ui-alert__icon">{ALERT_ICONS[tone]}</span>
      <div className="ui-alert__body">
        {title && <p className="ui-alert__title">{title}</p>}
        {children && <div className="ui-alert__text">{children}</div>}
      </div>
      {action}
      {onClose && <IconButton size="sm" label="Закрыть" icon={<X />} onClick={onClose} tooltip={false} />}
    </div>
  );
}
