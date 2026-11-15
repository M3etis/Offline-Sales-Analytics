import { forwardRef, useId } from 'react';
import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from 'react';
import { Search, X } from 'lucide-react';
import './Form.css';

interface FieldProps {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  children: (id: string, describedBy?: string) => ReactNode;
  className?: string;
  inline?: boolean;
}

/** Подпись + поле + подсказка/ошибка. children получает id для связки label → input */
export function Field({ label, hint, error, children, className = '', inline = false }: FieldProps) {
  const id = useId();
  const hintId = useId();
  const describedBy = error || hint ? hintId : undefined;
  return (
    <div className={`ui-field ${inline ? 'ui-field--inline' : ''} ${error ? 'ui-field--error' : ''} ${className}`}>
      <label className="ui-field__label" htmlFor={id}>
        {label}
      </label>
      {children(id, describedBy)}
      {(error || hint) && (
        <p id={hintId} className="ui-field__hint" role={error ? 'alert' : undefined}>
          {error || hint}
        </p>
      )}
    </div>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className = '', ...rest }, ref) {
  return <input ref={ref} className={`ui-input ${className}`} {...rest} />;
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select({ className = '', children, ...rest }, ref) {
  return (
    <select ref={ref} className={`ui-input ui-select ${className}`} {...rest}>
      {children}
    </select>
  );
});

interface SearchInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'onChange' | 'value'> {
  value: string;
  onChange: (value: string) => void;
  label?: string;
}

export function SearchInput({ value, onChange, label = 'Поиск', placeholder = 'Поиск', className = '', ...rest }: SearchInputProps) {
  return (
    <div className={`ui-search ${className}`}>
      <Search className="ui-search__icon" aria-hidden="true" />
      <input
        type="search"
        className="ui-input ui-search__input"
        aria-label={label}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape' && value) {
            e.stopPropagation();
            onChange('');
          }
        }}
        {...rest}
      />
      {value && (
        <button type="button" className="ui-search__clear" aria-label="Очистить поиск" onClick={() => onChange('')}>
          <X />
        </button>
      )}
    </div>
  );
}

interface SegmentedOption<T extends string> {
  value: T;
  label: ReactNode;
  icon?: ReactNode;
}

interface SegmentedProps<T extends string> {
  options: SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  label: string;
  size?: 'sm' | 'md';
}

export function Segmented<T extends string>({ options, value, onChange, label, size = 'md' }: SegmentedProps<T>) {
  return (
    <div className={`ui-segmented ui-segmented--${size}`} role="radiogroup" aria-label={label}>
      {options.map((opt) => {
        const selected = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={selected ? 0 : -1}
            className={`ui-segmented__item ${selected ? 'is-selected' : ''}`}
            onClick={() => onChange(opt.value)}
            onKeyDown={(e) => {
              if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
              e.preventDefault();
              const idx = options.findIndex((o) => o.value === value);
              const next = options[(idx + (e.key === 'ArrowRight' ? 1 : -1) + options.length) % options.length];
              onChange(next.value);
              const parent = e.currentTarget.parentElement;
              requestAnimationFrame(() => parent?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus());
            }}
          >
            {opt.icon}
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

interface ToggleChipProps {
  pressed: boolean;
  onChange: (pressed: boolean) => void;
  icon?: ReactNode;
  children: ReactNode;
  title?: string;
}

/** Переключатель-чип: «Графики», «Голос» */
export function ToggleChip({ pressed, onChange, icon, children, title }: ToggleChipProps) {
  return (
    <button type="button" aria-pressed={pressed} title={title} className={`ui-chip ${pressed ? 'is-on' : ''}`} onClick={() => onChange(!pressed)}>
      {icon}
      <span>{children}</span>
    </button>
  );
}

export function Checkbox({ label, checked, onChange, disabled, description }: { label: ReactNode; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; description?: ReactNode }) {
  const id = useId();
  return (
    <label className={`ui-checkbox ${disabled ? 'is-disabled' : ''}`} htmlFor={id}>
      <input id={id} type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="ui-checkbox__text">
        <span>{label}</span>
        {description && <span className="ui-checkbox__description">{description}</span>}
      </span>
    </label>
  );
}
