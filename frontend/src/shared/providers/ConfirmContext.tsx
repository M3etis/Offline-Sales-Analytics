import React, { createContext, useCallback, useContext, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Modal } from '../ui/Modal';
import { Button } from '../ui/Button';

export interface ConfirmOptions {
  /** Подпись кнопки подтверждения: «Удалить 12 сессий» */
  confirmLabel?: string;
  cancelLabel?: string;
  /** danger — красная кнопка для необратимых действий */
  tone?: 'danger' | 'default';
}

type ConfirmContextType = (message: string, title?: string, options?: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmContextType | undefined>(undefined);

export const useConfirm = () => {
  const context = useContext(ConfirmContext);
  if (!context) {
    throw new Error('useConfirm must be used within a ConfirmProvider');
  }
  return context;
};

interface ConfirmState {
  message: string;
  title: string;
  confirmLabel: string;
  cancelLabel: string;
  tone: 'danger' | 'default';
}

export const ConfirmProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [state, setState] = useState<ConfirmState | null>(null);
  const resolver = useRef<((value: boolean) => void) | null>(null);

  const confirm = useCallback<ConfirmContextType>((message, title, options) => {
    const opts = options ?? {};
    setState({
      message,
      title: title || 'Подтвердите действие',
      confirmLabel: opts.confirmLabel || 'Подтвердить',
      cancelLabel: opts.cancelLabel || 'Отмена',
      tone: opts.tone || 'danger',
    });
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve;
    });
  }, []);

  const settle = (value: boolean) => {
    setState(null);
    resolver.current?.(value);
    resolver.current = null;
  };

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Modal
        open={state !== null}
        onClose={() => settle(false)}
        title={state?.title}
        description={state?.message}
        size="sm"
        role="alertdialog"
        footer={
          <>
            <Button variant="secondary" onClick={() => settle(false)}>
              {state?.cancelLabel}
            </Button>
            <Button variant={state?.tone === 'danger' ? 'danger' : 'primary'} onClick={() => settle(true)}>
              {state?.confirmLabel}
            </Button>
          </>
        }
      />
    </ConfirmContext.Provider>
  );
};
