/**
 * Rules for application-form definitions: when a field is shown, when it is required, and how
 * sensitive it is. The same rules live in agent/form_rules.py (the assistants use that copy) and
 * must behave the same way. If you change one, change both.
 *
 * A field may carry three optional keys:
 *   visible_when   a condition; the field is shown only while it holds (default: always shown)
 *   required_when  a condition; the field is required while it holds (and it is shown)
 *   sensitive      "secret": the assistants never see it and it is masked (the SSN)
 *                  "health": the assistants may fill it, but it is never logged
 *
 * A condition is a map of field path -> expected value; every entry must match, an expected value
 * may be a list ("any of"), and a list of maps means "any of these maps holds". Values compare as
 * text, so a boolean is "true" or "false", and a field with no answer matches nothing.
 */
import type { FormCondition, FormFieldDef, FormSchema } from '../types';

export type Values = Record<string, string | undefined>;

function text(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return String(value);
}

export function hasValue(values: Values, path: string): boolean {
  return text(values[path]) !== '';
}

/** True when `condition` holds for `values`. No condition always holds. */
export function conditionHolds(condition: FormCondition | undefined | null, values: Values): boolean {
  if (!condition) return true;
  if (Array.isArray(condition)) {
    if (condition.length === 0) return true;
    return condition.some((c) => conditionHolds(c, values));
  }
  if (typeof condition !== 'object') return false;
  const entries = Object.entries(condition);
  if (entries.length === 0) return true;
  return entries.every(([path, expected]) => {
    const actual = text(values[path]);
    if (actual === '') return false;
    const options = Array.isArray(expected) ? expected : [expected];
    return options.map(text).includes(actual);
  });
}

export function isVisible(field: FormFieldDef, values: Values): boolean {
  return conditionHolds(field.visible_when, values);
}

export function isRequired(field: FormFieldDef, values: Values): boolean {
  if (!isVisible(field, values)) return false;
  if (field.required === true) return true;
  return Boolean(field.required_when) && conditionHolds(field.required_when, values);
}

export function fieldsOf(schema: FormSchema): FormFieldDef[] {
  return schema.sections.flatMap((s) => s.fields);
}

export function visibleFields(schema: FormSchema, values: Values): FormFieldDef[] {
  return fieldsOf(schema).filter((f) => isVisible(f, values));
}

export function requiredFields(schema: FormSchema, values: Values): FormFieldDef[] {
  return fieldsOf(schema).filter((f) => isRequired(f, values));
}

/** Fields that are shown, required, and still empty. */
export function missingRequired(schema: FormSchema, values: Values): FormFieldDef[] {
  return requiredFields(schema, values).filter((f) => !hasValue(values, f.path));
}

export type Sensitivity = 'secret' | 'health' | null;

export function sensitivity(field: FormFieldDef): Sensitivity {
  return field.sensitive === 'secret' || field.sensitive === 'health' ? field.sensitive : null;
}

/** False for "secret" fields: the assistants never fill or read them. */
export function assistantMayHandle(field: FormFieldDef): boolean {
  return sensitivity(field) !== 'secret';
}

/** `values` minus every secret field, for anything that is sent to an assistant. */
export function withoutSecretValues(schema: FormSchema, values: Record<string, string>): Record<string, string> {
  const secret = new Set(fieldsOf(schema).filter((f) => sensitivity(f) === 'secret').map((f) => f.path));
  return Object.fromEntries(Object.entries(values).filter(([path]) => !secret.has(path)));
}

/** Show only the last 4 characters of a secret value, for the PDF: "***-**-6789". */
export function maskSecret(value: string): string {
  const digits = value.replace(/[^0-9A-Za-z]/g, '');
  if (digits.length === 0) return '';
  return `${'*'.repeat(Math.max(digits.length - 4, 0))}${digits.slice(-4)}`;
}
