import { useEffect, useState } from 'react';

/**
 * Значение, которое догоняет исходное через delay мс после его последнего изменения.
 * Таймер отменяется при каждом новом значении и при размонтировании — отложенное
 * обновление не сработает после того, как значение сменилось.
 */
export function useDebouncedValue<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}
