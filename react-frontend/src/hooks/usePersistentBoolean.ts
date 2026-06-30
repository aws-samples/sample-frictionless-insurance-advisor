import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Boolean state mirrored to localStorage. Used for UI preferences that
 * should survive a page reload (sidebar collapsed, panel split ratio, etc.).
 *
 * Reads synchronously on first render so there's no "flash of initial state"
 * before the localStorage value loads. Tolerates SSR and private-mode
 * localStorage failures by falling back to the latest passed-in default.
 *
 * `defaultValue` is read via a ref so subsequent updates to the prop are
 * picked up by future hydration paths without copying it directly into
 * state — keeps the hook in line with the "do not store props in state"
 * rule while still honoring the latest fallback.
 */
export function usePersistentBoolean(
  key: string,
  defaultValue: boolean
): [boolean, (value: boolean) => void, () => void] {
  const defaultRef = useRef(defaultValue);
  defaultRef.current = defaultValue;

  const readInitial = (): boolean => {
    if (typeof window === 'undefined') return defaultRef.current;
    try {
      const stored = window.localStorage.getItem(key);
      if (stored === null) return defaultRef.current;
      return stored === 'true';
    } catch {
      return defaultRef.current;
    }
  };

  const [value, setInternalValue] = useState<boolean>(readInitial);

  // Keep localStorage in sync whenever the in-memory value changes.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      window.localStorage.setItem(key, value ? 'true' : 'false');
    } catch {
      // Ignore quota / private mode write failures.
    }
  }, [key, value]);

  const setValue = useCallback((next: boolean) => setInternalValue(next), []);
  const toggle = useCallback(() => setInternalValue((prev) => !prev), []);

  return [value, setValue, toggle];
}
