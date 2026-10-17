import {
  ReactNode,
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { loadRecommendation } from '../lib/api';
import type { GapLine } from '../lib/productLines';
import type { RecommendationResponse } from '../types';

interface UseRecommendationResult {
  recommendation: RecommendationResponse | null;
  loading: boolean;
  error: string | null;
}

interface RecommendationStore {
  /**
   * Read state for a customer/locale/line, kicking off a fetch if needed.
   * With no `line` nothing is fetched: the analysis only runs when the advisor asks for one.
   */
  get: (customerId: string | null, locale: string, line: GapLine | null) => UseRecommendationResult;
  /**
   * Start the fetch for a customer/locale/line if there is none yet. Idempotent. Called from an
   * effect, never during render: it updates the provider's state.
   */
  request: (customerId: string | null, locale: string, line: GapLine | null) => void;
  /** Drop every cached recommendation. Call this from the sidebar refresh. */
  clear: () => void;
}

/**
 * App-wide recommendation cache.
 *
 * Lives at the AuthedShell level (next to CustomersProvider) so the cache
 * survives switching between Text Assistant and Voice+Text Assistant pages.
 * Without this, the RecommendationSection unmounts each time the user
 * changes pages and we'd repay the ~5-10 second Bedrock cost.
 *
 * The store is keyed by `customerId|locale|line`. Switching the UI language
 * or the product line is a cache miss: the copy is translated server side and
 * each line is a separate analysis. The clear() handle is hooked up to the sidebar refresh button so
 * advisors can force a re-analysis after editing third-party policies.
 */
const RecommendationContext = createContext<RecommendationStore | null>(null);

interface Entry {
  state: UseRecommendationResult;
  promise: Promise<void> | null;
  // Bumped to a new object reference on clear() to invalidate any consumers
  // that have already keyed off this entry. Lets a single useState in the
  // provider trigger a re-render across all subscribers.
  generation: number;
}

const IDLE: UseRecommendationResult = { recommendation: null, loading: false, error: null };
const LOADING: UseRecommendationResult = { recommendation: null, loading: true, error: null };

const emptyEntry = (): Entry => ({
  state: { recommendation: null, loading: false, error: null },
  promise: null,
  generation: 0,
});

export function RecommendationProvider({ children }: { children: ReactNode }) {
  // Single source of truth - a Map of cacheKey -> Entry. Mutated in place,
  // and we bump `tick` to notify subscribers.
  const cacheRef = useRef<Map<string, Entry>>(new Map());
  const [tick, setTick] = useState(0);
  const bump = useCallback(() => setTick((n) => n + 1), []);

  const startFetch = useCallback(
    (cacheKey: string, customerId: string, locale: string, line: GapLine): void => {
      const entry = cacheRef.current.get(cacheKey) ?? emptyEntry();
      // Already fetching this key.
      if (entry.promise) return;

      entry.state = { recommendation: null, loading: true, error: null };
      entry.promise = loadRecommendation(customerId, locale, line)
        .then((data) => {
          // Only commit if the cache hasn't been cleared underneath us.
          const fresh = cacheRef.current.get(cacheKey);
          if (!fresh || fresh.generation !== entry.generation) return;
          fresh.state = { recommendation: data, loading: false, error: null };
          fresh.promise = null;
          bump();
        })
        .catch((err: unknown) => {
          const fresh = cacheRef.current.get(cacheKey);
          if (!fresh || fresh.generation !== entry.generation) return;
          const message = err instanceof Error ? err.message : String(err);
          fresh.state = { recommendation: null, loading: false, error: message };
          fresh.promise = null;
          bump();
        });

      cacheRef.current.set(cacheKey, entry);
      bump();
    },
    [bump]
  );

  // A pure read: no side effects, safe to call while rendering. An entry that does not
  // exist yet reads as "loading" because the consumer's effect is about to request it.
  const get = useCallback(
    (customerId: string | null, locale: string, line: GapLine | null): UseRecommendationResult => {
      if (!customerId || !line) return IDLE;
      const entry = cacheRef.current.get(`${customerId}|${locale}|${line}`);
      return entry ? entry.state : LOADING;
    },
    []
  );

  const request = useCallback(
    (customerId: string | null, locale: string, line: GapLine | null): void => {
      if (!customerId || !line) return;
      const cacheKey = `${customerId}|${locale}|${line}`;
      if (cacheRef.current.has(cacheKey)) return;
      startFetch(cacheKey, customerId, locale, line);
    },
    [startFetch]
  );

  const clear = useCallback(() => {
    // Bump the generation on each existing entry so any in-flight promise
    // resolves into a no-op. Then drop the map.
    for (const entry of cacheRef.current.values()) {
      entry.generation += 1;
    }
    cacheRef.current = new Map();
    bump();
  }, [bump]);

  // `tick` is in the dependencies on purpose: it is the cache's version, and a new context value
  // is what makes consumers re-render when an entry changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const value = useMemo<RecommendationStore>(() => ({ get, request, clear }), [get, request, clear, tick]);
  return createElement(RecommendationContext.Provider, { value }, children);
}

/**
 * Consumer hook used by RecommendationSection. Reads the cache for the
 * given customer/locale and triggers a fetch on miss. Same return shape
 * the previous local-only hook had, so callers don't need to change.
 */
export function useRecommendation(
  customerId: string | null,
  locale: string,
  line: GapLine | null
): UseRecommendationResult {
  const ctx = useContext(RecommendationContext);
  if (ctx === null) {
    throw new Error('useRecommendation must be used inside a RecommendationProvider');
  }
  const result = ctx.get(customerId, locale, line);
  // Starts the fetch on a miss. Runs after every render on purpose: it is a Map lookup when
  // the entry exists, and it is what restarts the fetch after clear() has emptied the cache.
  useEffect(() => {
    ctx.request(customerId, locale, line);
  });
  return result;
}

/**
 * Imperative cache reset, for the sidebar refresh button. Call this when
 * the customer list is refreshed so the next render of any selected
 * customer fires a new /recommend fetch.
 */
export function useClearRecommendations(): () => void {
  const ctx = useContext(RecommendationContext);
  if (ctx === null) {
    throw new Error('useClearRecommendations must be used inside a RecommendationProvider');
  }
  return ctx.clear;
}
