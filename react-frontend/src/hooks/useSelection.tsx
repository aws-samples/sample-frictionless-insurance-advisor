import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react';
import type { PriorityAction } from '../types';

/**
 * Which customer the advisor is working on, shared by the Home, Text and Voice pages so
 * that switching tab keeps the same customer. `select(null)` means "a new prospect" (no record
 * yet); `home()` clears both (the logo and the Home tab use it together with a switch to the Home tab).
 *
 * The state lives in the app shell (see `useSelectionState`) so the logo, which is outside
 * the pages, can reset it. It is kept in memory only: a reload starts on the Home tab.
 */
export interface SelectionApi {
  /** The customer being worked on, or null (nothing chosen, or a prospect when `prospectMode`). */
  selectedId: string | null;
  /** True while onboarding a new prospect who has no record yet. */
  prospectMode: boolean;
  /** A customer id, or null for a new prospect. */
  select: (customerId: string | null) => void;
  /** No customer and no prospect. */
  home: () => void;
  /** A Cockpit button was clicked and its starter prompt has not been sent yet. */
  pendingStart: { customerId: string; action: PriorityAction } | null;
  /**
   * Cockpit button: select the customer, make the Text Assistant the active tab, and queue
   * the starter prompt for the text chat. The chat clears it with `clearPendingStart`.
   */
  startFromCockpit: (customerId: string, action: PriorityAction) => void;
  /** The text chat sent the starter prompt. */
  clearPendingStart: () => void;
}

const SelectionContext = createContext<SelectionApi | null>(null);

/** Holds the selection. Called once, by the shell that renders `SelectionProvider`. */
export function useSelectionState(onStartFromCockpit?: () => void): SelectionApi {
  const [state, setState] = useState<{
    selectedId: string | null;
    prospectMode: boolean;
    pendingStart: SelectionApi['pendingStart'];
  }>({
    selectedId: null,
    prospectMode: false,
    pendingStart: null,
  });
  // The shell's tab switch; kept in a ref so `startFromCockpit` stays a stable function.
  const onStartRef = useRef(onStartFromCockpit);
  onStartRef.current = onStartFromCockpit;
  // Choosing another customer, or going Home, drops a starter prompt that was never sent.
  const select = useCallback((customerId: string | null) => {
    setState(
      customerId === null
        ? { selectedId: null, prospectMode: true, pendingStart: null }
        : { selectedId: customerId, prospectMode: false, pendingStart: null }
    );
  }, []);
  const home = useCallback(() => setState({ selectedId: null, prospectMode: false, pendingStart: null }), []);
  const startFromCockpit = useCallback((customerId: string, action: PriorityAction) => {
    setState({ selectedId: customerId, prospectMode: false, pendingStart: { customerId, action } });
    onStartRef.current?.();
  }, []);
  const clearPendingStart = useCallback(() => setState((prev) => (prev.pendingStart ? { ...prev, pendingStart: null } : prev)), []);
  return useMemo(
    () => ({ ...state, select, home, startFromCockpit, clearPendingStart }),
    [state, select, home, startFromCockpit, clearPendingStart]
  );
}

export function SelectionProvider({ value, children }: { value: SelectionApi; children: ReactNode }) {
  return <SelectionContext.Provider value={value}>{children}</SelectionContext.Provider>;
}

export function useSelection(): SelectionApi {
  const ctx = useContext(SelectionContext);
  if (!ctx) throw new Error('useSelection must be used inside a SelectionProvider');
  return ctx;
}
