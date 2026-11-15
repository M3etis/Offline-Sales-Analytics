/** Ключ localStorage с последним открытым диалогом ассистента: /analyst продолжает его */
export const CURRENT_SESSION_KEY = 'currentSessionId';

/** Следующее открытие /analyst начнёт новый диалог, а не продолжит последний */
export function forgetCurrentSession() {
  try {
    localStorage.removeItem(CURRENT_SESSION_KEY);
  } catch {
    /* private mode */
  }
}
