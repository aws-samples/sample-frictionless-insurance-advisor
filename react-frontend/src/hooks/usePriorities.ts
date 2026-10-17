import { useCallback, useEffect, useState } from 'react';
import { friendlyApiError, loadPriorities } from '../lib/api';
import type { PrioritiesResponse } from '../types';

interface UsePrioritiesResult {
  data: PrioritiesResponse | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * The advisor's ranked to-do list for the Cockpit. Loaded when the welcome
 * area appears and again on request; the ranking itself is computed by the
 * service (lambda/priorities), not here.
 */
export function usePriorities(): UsePrioritiesResult {
  const [data, setData] = useState<PrioritiesResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState<number>(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    loadPriorities()
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(friendlyApiError(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tick]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, loading, error, reload };
}
