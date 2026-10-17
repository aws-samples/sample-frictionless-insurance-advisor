import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  type Dispatch,
  type ReactNode,
} from 'react';
import type { Customer, FormFieldState, FormFillEvent, FormSchema } from '../types';
import {
  PROSPECT_KEY,
  applyFill,
  closeForm,
  computeProgress,
  confirmField,
  emptySession,
  openForm,
  resetToPrefill,
  setManual,
  syncPrefill,
  type FormFillProgress,
  type FormPrefill,
  type FormSession,
  type Sessions,
} from './formSessions';

/**
 * Stand-in schema used only while no form is open, so progress has something to be
 * computed against.
 */
const EMPTY_SCHEMA: FormSchema = {
  form_id: 'none',
  product_type: 'none',
  product_name: '',
  title: '',
  sections: [],
};

/** The slot for the Home screen (no customer, not a prospect). A form is never opened there. */
const HOME_KEY = '__home__';

/**
 * Seed the form with what the CRM already knows about the customer, so the
 * advisor never has to dictate or type details we hold on file. Only fields
 * the profile can actually answer; the rest come from the conversation.
 * Paths that a given product's form does not have are simply never shown.
 */
export function prefillFromCustomer(customer: Customer | null): FormPrefill {
  if (!customer) return {};
  const prefill: FormPrefill = {};
  const put = (path: string, value: string | number | boolean | undefined | null) => {
    if (value === undefined || value === null || value === '') return;
    prefill[path] = String(value);
  };
  put('applicant.full_name', customer.name);
  put('applicant.date_of_birth', customer.date_of_birth);
  put('applicant.gender', customer.gender);
  put('applicant.occupation', customer.occupation);
  put('applicant.annual_income', customer.annual_income);
  put('applicant.email', customer.email);
  put('applicant.phone', customer.phone);
  put('applicant.address', customer.address);
  put('health.smoker', customer.smoking);
  put('health.existing_conditions', customer.medical_conditions);
  // A home owner's mailing address is normally the property being insured.
  // The advisor can change it; it is only a starting point.
  if (customer.home_owner) put('property.address', customer.address);
  return prefill;
}

// ---- the shared store ---------------------------------------------------------------------

type Action =
  | { type: 'sync'; key: string; prefill: FormPrefill }
  | { type: 'open'; key: string; prefill: FormPrefill; schema: FormSchema }
  | { type: 'close'; key: string }
  | { type: 'fill'; key: string; prefill: FormPrefill; event: FormFillEvent }
  | { type: 'manual'; key: string; path: string; value: string }
  | { type: 'confirm'; key: string; path: string }
  | { type: 'reset'; key: string; prefill: FormPrefill };

export function sessionsReducer(state: Sessions, action: Action): Sessions {
  const current = state[action.key];
  const base = current ?? emptySession();
  let next: FormSession;
  switch (action.type) {
    case 'sync':
      next = syncPrefill(base, action.prefill);
      break;
    // Opening and filling first bring the profile values up to date, so a form opened (or an
    // answer arriving) before the sync effect has run is still built on the current profile.
    case 'open':
      next = openForm(syncPrefill(base, action.prefill), action.schema);
      break;
    case 'close':
      next = closeForm(base);
      break;
    case 'fill':
      next = applyFill(syncPrefill(base, action.prefill), action.event);
      break;
    case 'manual':
      next = setManual(base, action.path, action.value);
      break;
    case 'confirm':
      next = confirmField(base, action.path);
      break;
    case 'reset':
      next = resetToPrefill(base, action.prefill);
      break;
  }
  if (current !== undefined && next === current) return state;
  return { ...state, [action.key]: next };
}

interface Store {
  sessions: Sessions;
  dispatch: Dispatch<Action>;
}

const FormSessionsContext = createContext<Store | null>(null);

/**
 * Holds every customer's application-form state for the whole signed-in visit.
 * Mounted above the pages so it survives switching Text and Voice, going Home, and
 * opening another customer. It is dropped on sign-out, because the app unmounts.
 */
export function FormSessionsProvider({ children }: { children: ReactNode }) {
  const [sessions, dispatch] = useReducer(sessionsReducer, {} as Sessions);
  const value = useMemo<Store>(() => ({ sessions, dispatch }), [sessions]);
  return createElement(FormSessionsContext.Provider, { value }, children);
}

// ---- the hook the pages use ---------------------------------------------------------------

export type { FormFillProgress, FormPrefill };

export interface ApplicationForm {
  /** The form that is showing, or null when the profile is. */
  activeForm: FormSchema | null;
  /** Answers so far, by field path: from the profile, the assistant or the advisor. */
  fields: Record<string, FormFieldState>;
  progress: FormFillProgress;
  /** Field paths answered in a previous product's form and kept for this one. */
  carriedOver: Set<string>;
  /** Field values known from the customer's record. */
  prefill: FormPrefill;
  /** Open a form. Used by the profile's button and by the assistants, so both give the same view. */
  openForm: (productType: string, schema: FormSchema) => void;
  /** A value from the assistant. */
  fillField: (event: FormFillEvent) => void;
  /** The advisor typed or chose a value. It wins over later assistant fills. */
  setFieldManually: (path: string, value: string) => void;
  /** The advisor accepts an amber value. */
  confirmField: (path: string) => void;
  /** Back to the profile. The answers are kept. */
  closeForm: () => void;
  /** Throw the answers away and start again from the profile. */
  reset: () => void;
  /** The open form and its current values, for the assistant's next turn. */
  getFormContext: () => { productType: string; values: Record<string, string> } | null;
}

/**
 * The application form for the selected customer, shared by the Text and Voice pages.
 *
 * It is a view onto the shared store, so a form opened from the profile and one opened by the
 * assistant are the same form with the same answers, and nothing entered is lost when the
 * advisor moves around. `prospectMode` is the "+ New Prospect" flow, which has no customer yet.
 */
export function useApplicationForm(customer: Customer | null, prospectMode = false): ApplicationForm {
  const store = useContext(FormSessionsContext);
  if (store === null) throw new Error('useApplicationForm must be used inside a FormSessionsProvider');
  const { sessions, dispatch } = store;

  const key = customer ? customer.customer_id : prospectMode ? PROSPECT_KEY : HOME_KEY;

  // Keyed on the content, not the customer object. The customer list is refetched after a note
  // is saved or the sidebar is refreshed, which hands back new objects with the same data.
  const prefillKey = JSON.stringify(prefillFromCustomer(customer));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const prefill = useMemo<FormPrefill>(() => JSON.parse(prefillKey), [prefillKey]);

  // Keep the stored profile values current (the assistant may have saved a new phone number).
  useEffect(() => {
    dispatch({ type: 'sync', key, prefill });
  }, [dispatch, key, prefill]);

  // What the stored session will be once the effect above has run, so the first render of a
  // customer already shows their profile values instead of an empty form for one frame.
  const stored = sessions[key];
  const session = useMemo<FormSession>(() => syncPrefill(stored ?? emptySession(), prefill), [stored, prefill]);

  const { activeForm, fields } = session;
  const progress = useMemo(() => computeProgress(activeForm ?? EMPTY_SCHEMA, fields), [activeForm, fields]);
  const carriedOver = useMemo(() => new Set(session.carriedOver), [session.carriedOver]);

  const open = useCallback(
    (_productType: string, schema: FormSchema) => dispatch({ type: 'open', key, prefill, schema }),
    [dispatch, key, prefill]
  );
  const fillField = useCallback(
    (event: FormFillEvent) => dispatch({ type: 'fill', key, prefill, event }),
    [dispatch, key, prefill]
  );
  const setFieldManually = useCallback(
    (path: string, value: string) => dispatch({ type: 'manual', key, path, value }),
    [dispatch, key]
  );
  const confirm = useCallback((path: string) => dispatch({ type: 'confirm', key, path }), [dispatch, key]);
  const close = useCallback(() => dispatch({ type: 'close', key }), [dispatch, key]);
  const reset = useCallback(() => dispatch({ type: 'reset', key, prefill }), [dispatch, key, prefill]);

  const getFormContext = useCallback(() => {
    if (!activeForm) return null;
    const values: Record<string, string> = {};
    for (const [path, state] of Object.entries(fields)) {
      if (state.value !== '') values[path] = state.value;
    }
    return { productType: activeForm.product_type, values };
  }, [activeForm, fields]);

  return {
    activeForm,
    fields,
    progress,
    carriedOver,
    prefill,
    openForm: open,
    fillField,
    setFieldManually,
    confirmField: confirm,
    closeForm: close,
    reset,
    getFormContext,
  };
}
