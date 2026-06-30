import {
  ReactNode,
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react';

import { loadCustomersWithPolicies } from '../lib/api';
import type { Customer } from '../types';

interface UseCustomersResult {
  customers: Customer[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

/**
 * App-wide customer + policy store.
 *
 * Mounted once inside AuthedShell after Cognito sign-in completes, so the
 * /profile and /policy GETs run exactly once per session (instead of being
 * re-issued every time the advisor switches between the Text Assistant and
 * Voice+Text Assistant pages). Both pages consume from this store via the
 * `useCustomers` hook below.
 *
 * Refreshes happen on demand via the sidebar's refresh button. New prospects
 * created by the agent through `create_profile` won't appear until the
 * advisor clicks refresh — same trade-off as before; we explicitly traded
 * staleness for speed.
 */
const CustomersContext = createContext<UseCustomersResult | null>(null);

export function CustomersProvider({ children }: { children: ReactNode }) {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  // Guards against React StrictMode's double-effect in dev (which would
  // otherwise fire two parallel /profile + /policy round trips on mount).
  const inFlightRef = useRef<Promise<void> | null>(null);

  const load = useCallback(async (): Promise<void> => {
    if (inFlightRef.current) return inFlightRef.current;
    setLoading(true);
    setError(null);
    const promise = (async () => {
      try {
        const next = await loadCustomersWithPolicies();
        setCustomers(next);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
        inFlightRef.current = null;
      }
    })();
    inFlightRef.current = promise;
    return promise;
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const value: UseCustomersResult = { customers, loading, error, refresh: load };
  return createElement(CustomersContext.Provider, { value }, children);
}

/**
 * Consumer hook — reads from the shared CustomersProvider. Throws if used
 * outside a provider so missing wrapping is caught immediately in dev.
 */
export function useCustomers(): UseCustomersResult {
  const ctx = useContext(CustomersContext);
  if (ctx === null) {
    throw new Error('useCustomers must be used inside a CustomersProvider');
  }
  return ctx;
}
