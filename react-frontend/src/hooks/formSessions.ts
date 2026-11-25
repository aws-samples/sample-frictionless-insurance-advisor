import type { FieldConfidence, FormFieldState, FormFillEvent, FormSchema } from '../types';
import { assistantMayHandle, hasValue, requiredFields, visibleFields } from '../lib/formRules';

/**
 * The state of the application forms, one session per customer.
 *
 * The form is the same wherever it is opened from (the profile's button, the text
 * assistant, the voice assistant) because they all end up in this one store, and the
 * store lives above the pages. So what has been entered stays put when the advisor
 * switches between the Text and Voice pages, goes Home, opens another customer and
 * comes back, or closes the form and reopens it.
 *
 * This file is pure (no React): the rules are easy to test on their own.
 */

/** At/above this confidence a value is taken as-is (green). Below it, amber. */
const HIGH_THRESHOLD = 0.85;

export function tierFor(confidence: number): FieldConfidence {
  return confidence >= HIGH_THRESHOLD ? 'high' : 'review';
}

/** Field values known before the conversation starts, keyed by field path. */
export type FormPrefill = Record<string, string>;

export interface FormSession {
  /** The form that is showing, or null when the profile is. */
  activeForm: FormSchema | null;
  /** Product type of the form opened last. Decides whether answers count as carried over. */
  lastProductType: string | null;
  fields: Record<string, FormFieldState>;
  /** Field paths answered in a previous product's form and kept for this one. */
  carriedOver: string[];
  /**
   * The reference of the last save of each product's application. Saving that form again
   * overwrites that save instead of adding a new one. It lives here, not in the form panel,
   * so it survives closing and reopening the form and moving between the Text and Voice pages.
   */
  savedRefs: Record<string, string>;
}

/** One slot per customer. A prospect with no profile yet has a slot of their own. */
export const PROSPECT_KEY = '__prospect__';

export type Sessions = Record<string, FormSession>;

export function emptySession(): FormSession {
  return { activeForm: null, lastProductType: null, fields: {}, carriedOver: [], savedRefs: {} };
}

function fromProfile(value: string): FormFieldState {
  return { value, confidence: 1, tier: 'high', origin: 'profile' };
}

/**
 * Bring the profile-sourced fields up to date with the prefill.
 *
 * Only fields that still hold what the profile gave us change. Anything the assistant
 * filled or the advisor typed is left alone: the profile gaining a phone number, say,
 * must not wipe a form that is half done.
 */
export function syncPrefill(session: FormSession, prefill: FormPrefill): FormSession {
  const fields = { ...session.fields };
  let changed = false;
  for (const [path, value] of Object.entries(prefill)) {
    if (value === '' || value === undefined) continue;
    const current = fields[path];
    if (!current) {
      fields[path] = fromProfile(value);
      changed = true;
    } else if (current.origin === 'profile' && current.value !== value) {
      fields[path] = fromProfile(value);
      changed = true;
    }
  }
  // A value the profile no longer has disappears, but only if nobody has touched the field.
  for (const [path, state] of Object.entries(fields)) {
    if (state.origin === 'profile' && !(path in prefill)) {
      delete fields[path];
      changed = true;
    }
  }
  return changed ? { ...session, fields } : session;
}

/** Field paths a schema asks for. */
export function pathsOf(schema: FormSchema): string[] {
  return schema.sections.flatMap((s) => s.fields.map((f) => f.path));
}

/**
 * A form takes over the left panel. Answers are kept (most products ask the same applicant,
 * health and beneficiary questions). They are flagged as carried over only when this is a
 * different product from the one before: reopening the same form is not carrying anything over.
 */
export function openForm(session: FormSession, schema: FormSchema): FormSession {
  const sameProduct = session.lastProductType !== null && session.lastProductType === schema.product_type;
  const carriedOver = sameProduct
    ? session.carriedOver.filter((p) => pathsOf(schema).includes(p))
    : pathsOf(schema).filter((p) => {
        const existing = session.fields[p];
        // Profile prefill isn't "carried over": it was never entered in a form, it is what we knew.
        return existing?.value && existing.origin !== 'profile';
      });
  return { ...session, activeForm: schema, lastProductType: schema.product_type, carriedOver };
}

/** Back to the profile. The answers stay. */
export function closeForm(session: FormSession): FormSession {
  return session.activeForm ? { ...session, activeForm: null } : session;
}

/** A value from the assistant. Never overwrites something the advisor typed. */
export function applyFill(session: FormSession, event: FormFillEvent): FormSession {
  const { path, value, confidence, source } = event;
  if (session.fields[path]?.origin === 'manual') return session;
  // A secret field (the SSN) is typed by the advisor only. The assistants are refused server-side;
  // this keeps a stray fill event from landing in it anyway.
  const def = session.activeForm?.sections.flatMap((s) => s.fields).find((f) => f.path === path);
  if (def && !assistantMayHandle(def)) return session;
  return {
    ...session,
    fields: { ...session.fields, [path]: { value, confidence, tier: tierFor(confidence), source, origin: 'voice' } },
  };
}

/** The advisor typed or chose a value. It becomes authoritative. */
export function setManual(session: FormSession, path: string, value: string): FormSession {
  return {
    ...session,
    fields: {
      ...session.fields,
      [path]: { value, confidence: 1, tier: 'high', source: session.fields[path]?.source, origin: 'manual' },
    },
    carriedOver: session.carriedOver.filter((p) => p !== path),
  };
}

/** The advisor has looked at an amber value and accepts it. */
export function confirmField(session: FormSession, path: string): FormSession {
  const current = session.fields[path];
  if (!current) return session;
  return { ...session, fields: { ...session.fields, [path]: { ...current, tier: 'high', origin: 'manual' } } };
}

/** Remember the reference a save came back with, so the next save of this product replaces it. */
export function rememberSavedRef(session: FormSession, productType: string, ref: string): FormSession {
  return session.savedRefs[productType] === ref
    ? session
    : { ...session, savedRefs: { ...session.savedRefs, [productType]: ref } };
}

/** Throw away every answer and start from the profile again. A fresh start is a new application. */
export function resetToPrefill(session: FormSession, prefill: FormPrefill): FormSession {
  return syncPrefill({ ...session, fields: {}, carriedOver: [], savedRefs: {} }, prefill);
}

// ---- progress ---------------------------------------------------------------------------------

export interface FormFillProgress {
  /** Required fields that currently hold a value. */
  requiredFilled: number;
  requiredTotal: number;
  /** Percentage of required fields filled, 0-100. */
  percentage: number;
  /** Filled fields still flagged amber. */
  pendingReview: number;
  /** Labels of required fields with no value yet. */
  missingLabels: string[];
}

/** The current answers as a plain path -> value map, for the form rules. */
export function valuesOf(fields: Record<string, FormFieldState>): Record<string, string> {
  const values: Record<string, string> = {};
  for (const [path, state] of Object.entries(fields)) values[path] = state.value;
  return values;
}

/**
 * Progress counts only fields that are shown and required right now (see lib/formRules.ts), so a
 * detail box that appears after a Yes answer joins the total, and a hidden field never blocks Send.
 */
export function computeProgress(schema: FormSchema, fields: Record<string, FormFieldState>): FormFillProgress {
  const values = valuesOf(fields);
  const required = requiredFields(schema, values);
  const shown = new Set(visibleFields(schema, values).map((f) => f.path));
  const filled = required.filter((f) => hasValue(values, f.path));
  return {
    requiredFilled: filled.length,
    requiredTotal: required.length,
    percentage: required.length === 0 ? 0 : Math.round((filled.length / required.length) * 100),
    pendingReview: Object.entries(fields).filter(([path, f]) => f.tier === 'review' && shown.has(path)).length,
    missingLabels: required.filter((f) => !hasValue(values, f.path)).map((f) => f.label),
  };
}
