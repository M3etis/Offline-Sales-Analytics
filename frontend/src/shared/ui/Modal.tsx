import { useEffect, useId, useRef } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { IconButton } from './Button';
import './Modal.css';

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  size?: 'sm' | 'md' | 'lg';
  role?: 'dialog' | 'alertdialog';
}

const FOCUSABLE = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Modal({ open, onClose, title, description, children, footer, size = 'md', role = 'dialog' }: ModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const descId = useId();
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const dialog = dialogRef.current;

    // Первый фокус: первое поле ввода, иначе первая кнопка в футере, иначе сам диалог
    const focusTarget =
      dialog?.querySelector<HTMLElement>('input:not([disabled]), textarea:not([disabled]), select:not([disabled])') ??
      dialog?.querySelector<HTMLElement>('.ui-modal__footer button:not([disabled])') ??
      dialog;
    focusTarget?.focus();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key === 'Tab' && dialog) {
        const items = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.offsetParent !== null);
        if (items.length === 0) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    document.addEventListener('keydown', onKeyDown, true);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      document.body.style.overflow = prevOverflow;
      previouslyFocused?.focus?.();
    };
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div
      className="ui-modal-overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className={`ui-modal ui-modal--${size}`}
        role={role}
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descId : undefined}
        tabIndex={-1}
      >
        <div className="ui-modal__header">
          <div className="ui-modal__heading">
            <h2 id={titleId} className="ui-modal__title">
              {title}
            </h2>
            {description && (
              <p id={descId} className="ui-modal__description">
                {description}
              </p>
            )}
          </div>
          <IconButton label="Закрыть" icon={<X />} size="sm" onClick={onClose} tooltip={false} />
        </div>
        {children && <div className="ui-modal__body">{children}</div>}
        {footer && <div className="ui-modal__footer">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}
