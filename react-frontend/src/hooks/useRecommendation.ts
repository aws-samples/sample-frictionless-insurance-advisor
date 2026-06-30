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

import { loadRecommendation } from '../lib/api';
import type { RecommendationResponse } from '../types';

interface UseRecommendationResult {
  recommendation: RecommendationResponse | null;
  loading: boolean;
  error: string | null;
}

interface RecommendationStore {
  /** Read state for a customer/locale pair, kicking off a fetch if needed. */
  get: (customerId: string | null, locale: string) => UseRecommendationResult;
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
 * The store is keyed by `customerId|locale`. Switching the UI language is
 * also a cache miss because the recommendation copy is translated server
 * side. The clear() handle is hooked up to the sidebar refresh button so
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

const emptyEntry = (): Entry => ({
  state: { recommendation: null, loading: false, error: null },
  promise: null,
  generation: 0,
});

export function RecommendationProvider({ children }: { children: ReactNode }) {
  // Single source of truth - a Map of cacheKey -> Entry. Mutated in place,
  // and we bump `tick` to notify subscribers.
  const cacheRef = useRef<Map<string, Entry>>(new Map());
  const [, setTick] = useState(0);
  const bump = useCallback(() => setTick((n) => n + 1), []);

  const startFetch = useCallback(
    (cacheKey: string, customerId: string, locale: string): void => {
      const entry = cacheRef.current.get(cacheKey) ?? emptyEntry();
      // Already fetching this key.
      if (entry.promise) return;

      entry.state = { recommendation: null, loading: true, error: null };
      entry.promise = loadRecommendation(customerId, locale)
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

  const get = useCallback(
    (customerId: string | null, locale: string): UseRecommendationResult => {
      if (!customerId) {
        return { recommendation: null, loading: false, error: null };
      }
      const cacheKey = `${customerId}|${locale}`;
      const entry = cacheRef.current.get(cacheKey);

      if (!entry) {
        // Lazily kick off a fetch on the first read for this key. Doing it
        // inline here (rather than from a component effect) keeps the cache
        // shared across pages: any component that asks for a (customer,
        // locale) pair will see the same in-flight promise.
        startFetch(cacheKey, customerId, locale);
        // Return a transient loading state so the consumer renders the
        // spinner immediately (before bump() schedules the next render).
        return { recommendation: null, loading: true, error: null };
      }
      return entry.state;
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

  const value: RecommendationStore = { get, clear };
  return createElement(RecommendationContext.Provider, { value }, children);
}

/**
 * Consumer hook used by RecommendationSection. Reads the cache for the
 * given customer/locale and triggers a fetch on miss. Same return shape
 * the previous local-only hook had, so callers don't need to change.
 */
export function useRecommendation(
  customerId: string | null,
  locale: string
): UseRecommendationResult {
  const ctx = useContext(RecommendationContext);
  if (ctx === null) {
    throw new Error('useRecommendation must be used inside a RecommendationProvider');
  }
  // Read once per render. Note get() may schedule a state bump in the
  // provider (kicking off a fetch); we don't read its updated state in this
  // call, the next render will pick it up.
  // eslint-disable-next-line react-hooks/exhaustive-deps -- ctx.get is stable
  const result = ctx.get(customerId, locale);

  // Useful in dev: ensure unmounted consumers don't hold us up. No-op in
  // prod; left here to make the lifecycle explicit.
  useEffect(() => undefined, [customerId, locale]);

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
