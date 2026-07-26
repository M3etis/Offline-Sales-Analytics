import React, { createContext, useContext, useState } from 'react';
import type { ReactNode } from 'react';

type ConfirmContextType = (message: string, title?: string, timeout?: number) => Promise<boolean>;


const ConfirmContext = createContext<ConfirmContextType | undefined>(undefined);

export const useConfirm = () => {
  const context = useContext(ConfirmContext);
  if (!context) {
    throw new Error('useConfirm must be used within a ConfirmProvider');
  }
  return context;
};

export const ConfirmProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [message, setMessage] = useState('');
  const [title, setTitle] = useState('');
  const [resolvePromise, setResolvePromise] = useState<(value: boolean) => void>();

  const confirm = (msg: string, t?: string, _timeout?: number) => {
    setMessage(msg);
    setTitle(t || 'Подтверждение');
    setIsOpen(true);
    return new Promise<boolean>((resolve) => {
      setResolvePromise(() => resolve);
    });
  };

  const handleConfirm = () => {
    setIsOpen(false);
    if (resolvePromise) resolvePromise(true);
  };

  const handleCancel = () => {
    setIsOpen(false);
    if (resolvePromise) resolvePromise(false);
  };

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {isOpen && (
        <div className="modal-overlay">
          <div className="modal-content">
            <h3>{title}</h3>
            <p style={{ whiteSpace: "pre-wrap", textAlign: "left", lineHeight: "1.5", margin: "1rem 0" }}>{message}</p>
            <div className="modal-actions">
              <button className="btn btn--secondary" onClick={handleCancel}>Отмена</button>
              <button className="btn btn--danger" onClick={handleConfirm}>Подтвердить</button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
};
