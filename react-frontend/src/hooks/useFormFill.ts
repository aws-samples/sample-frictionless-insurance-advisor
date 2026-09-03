import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type {
  FieldConfidence,
  FormFieldState,
  FormFillEvent,
  FormSchema,
} from '../types';

/** At/above this confidence a value is taken as-is (green). Below it, amber. */
const HIGH_THRESHOLD = 0.85;

export function tierFor(confidence: number): FieldConfidence {
  return confidence >= HIGH_THRESHOLD ? 'high' : 'review';
}

/** Field values known before the conversation starts, keyed by field path. */
export type FormPrefill = Record<string, string>;

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

export interface UseFormFillResult {
  fields: Record<string, FormFieldState>;
  progress: FormFillProgress;
  /** Apply an extraction result (from the agent, or the demo script). */
  applyFill: (event: FormFillEvent) => void;
  /** Advisor typed/selected a value by hand. Becomes authoritative. */
  setFieldManually: (path: string, value: string) => void;
  /** Clear the amber flag once the advisor has eyeballed the value. */
  confirmField: (path: string) => void;
  /** Back to just the prefilled values. */
  reset: () => void;
  /**
   * Field paths carried over from a previous product's form — i.e. already
   * answered before this schema was opened. Lets the UI show the advisor that
   * their earlier answers were kept rather than silently re-appearing.
   */
  carriedOver: Set<string>;
  /** Called when a new schema takes over, to recompute the carried-over set. */
  noteSchemaChange: (schema: FormSchema) => void;
}

function stateFromPrefill(prefill: FormPrefill): Record<string, FormFieldState> {
  const out: Record<string, FormFieldState> = {};
  for (const [path, value] of Object.entries(prefill)) {
    if (value === '' || value === undefined) continue;
    out[path] = {
      value,
      confidence: 1,
      tier: 'high',
      origin: 'profile',
    };
  }
  return out;
}

/**
 * Owns the state of one application form: field values, their provenance and
 * confidence.
 *
 * Fields start populated from what we already know about the customer (their
 * CRM profile), then the conversation fills the rest. Every extraction is
 * written straight into the form; anything below the high-confidence bar is
 * flagged amber for the advisor to confirm rather than hidden away.
 *
 * Precedence: manual > voice > profile. Once the advisor types in a field,
 * later extractions leave it alone.
 */
export function useFormFill(schema: FormSchema, prefill: FormPrefill): UseFormFillResult {
  const [fields, setFields] = useState<Record<string, FormFieldState>>(() =>
    stateFromPrefill(prefill)
  );
  const [carriedOver, setCarriedOver] = useState<Set<string>>(() => new Set());

  // Re-seed when the prefill changes (different customer selected). A new
  // customer means a genuinely new application, so nothing carries over.
  useEffect(() => {
    setFields(stateFromPrefill(prefill));
    setCarriedOver(new Set());
  }, [prefill]);

  // Mirrors `fields` for use inside callbacks without adding it as a dep,
  // which would re-create applyFill on every keystroke.
  const fieldsRef = useRef<Record<string, FormFieldState>>({});
  fieldsRef.current = fields;

  const applyFill = useCallback((event: FormFillEvent) => {
    const { path, value, confidence, source } = event;
    // Never clobber a human edit.
    if (fieldsRef.current[path]?.origin === 'manual') return;

    setFields((prev) => ({
      ...prev,
      [path]: { value, confidence, tier: tierFor(confidence), source, origin: 'voice' },
    }));
  }, []);

  const setFieldManually = useCallback((path: string, value: string) => {
    setFields((prev) => ({
      ...prev,
      [path]: {
        value,
        confidence: 1,
        tier: 'high',
        source: prev[path]?.source,
        origin: 'manual',
      },
    }));
  }, []);

  const confirmField = useCallback((path: string) => {
    setFields((prev) => {
      const current = prev[path];
      if (!current) return prev;
      return { ...prev, [path]: { ...current, tier: 'high', origin: 'manual' } };
    });
  }, []);

  const reset = useCallback(() => {
    setFields(stateFromPrefill(prefill));
    setCarriedOver(new Set());
  }, [prefill]);

  /**
   * A different product's form has taken over. Field state is deliberately
   * NOT cleared: most life products ask the same applicant, health and
   * beneficiary questions, so anything already answered should stand rather
   * than being asked again. Values whose path isn't in the new schema simply
   * stop rendering, and come back if the advisor switches back.
   */
  const noteSchemaChange = useCallback((next: FormSchema) => {
    const nextPaths = next.sections.flatMap((s) => s.fields.map((f) => f.path));
    setCarriedOver(
      new Set(
        nextPaths.filter((p) => {
          const existing = fieldsRef.current[p];
          // Profile prefill isn't "carried over" — it was never entered in a
          // previous form, it's just what we already knew.
          return existing?.value && existing.origin !== 'profile';
        })
      )
    );
  }, []);

  const progress = useMemo<FormFillProgress>(() => {
    const required = schema.sections.flatMap((s) => s.fields.filter((f) => f.required));
    const filled = required.filter((f) => {
      const v = fields[f.path]?.value;
      return v !== undefined && v !== '';
    });
    const pendingReview = Object.values(fields).filter((f) => f.tier === 'review').length;
    const missingLabels = required
      .filter((f) => {
        const v = fields[f.path]?.value;
        return v === undefined || v === '';
      })
      .map((f) => f.label);

    return {
      requiredFilled: filled.length,
      requiredTotal: required.length,
      percentage: required.length === 0 ? 0 : Math.round((filled.length / required.length) * 100),
      pendingReview,
      missingLabels,
    };
  }, [schema, fields]);

  return {
    fields,
    progress,
    applyFill,
    setFieldManually,
    confirmField,
    reset,
    carriedOver,
    noteSchemaChange,
  };
}
