import { forwardRef } from 'react';
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import './Button.css';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'accent' | 'danger' | 'danger-outline';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: ReactNode;
  iconRight?: ReactNode;
  loading?: boolean;
  block?: boolean;
}

export function Spinner({ size = 14 }: { size?: number }) {
  return <span className="ui-spinner" style={{ width: size, height: size }} aria-hidden="true" />;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', icon, iconRight, loading = false, block = false, className = '', children, disabled, type = 'button', ...rest },
  ref,
) {
  const classes = [
    'ui-btn',
    `ui-btn--${variant}`,
    `ui-btn--${size}`,
    block ? 'ui-btn--block' : '',
    className,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <button ref={ref} type={type} className={classes} disabled={disabled || loading} aria-busy={loading || undefined} {...rest}>
      {loading ? <Spinner /> : icon}
      {children !== undefined && children !== null && <span className="ui-btn__label">{children}</span>}
      {iconRight}
    </button>
  );
});

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  /** Обязательная подпись: aria-label и тултип */
  label: string;
  icon: ReactNode;
  variant?: 'ghost' | 'danger' | 'outline' | 'primary';
  size?: 'sm' | 'md';
  active?: boolean;
  tooltip?: boolean;
  tooltipPlace?: 'top' | 'bottom' | 'left' | 'right';
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon, variant = 'ghost', size = 'md', active = false, tooltip = true, tooltipPlace = 'top', className = '', type = 'button', ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      aria-pressed={active || undefined}
      className={`ui-icon-btn ui-icon-btn--${variant} ui-icon-btn--${size} ${active ? 'is-active' : ''} ${className}`}
      data-tooltip-id={tooltip ? 'ui-tooltip' : undefined}
      data-tooltip-content={tooltip ? label : undefined}
      data-tooltip-place={tooltip ? tooltipPlace : undefined}
      {...rest}
    >
      {icon}
    </button>
  );
});
