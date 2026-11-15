import { useCallback, useEffect, useState } from 'react';

export type Theme = 'dark' | 'light';

const STORAGE_KEY = 'theme';

function readTheme(): Theme {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'light' ? 'light' : 'dark';
  } catch {
    return 'dark';
  }
}

export function applyStoredTheme() {
  document.documentElement.setAttribute('data-theme', readTheme());
}

const listeners = new Set<(t: Theme) => void>();

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(readTheme);

  useEffect(() => {
    listeners.add(setThemeState);
    return () => {
      listeners.delete(setThemeState);
    };
  }, []);

  const setTheme = useCallback((next: Theme) => {
    document.documentElement.setAttribute('data-theme', next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* private mode */
    }
    listeners.forEach((fn) => fn(next));
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(readTheme() === 'dark' ? 'light' : 'dark');
  }, [setTheme]);

  return { theme, setTheme, toggleTheme };
}
