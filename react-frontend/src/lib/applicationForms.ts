import { loadDataFile } from './api';
import type { ProductLine } from './productLines';
import type { FormSchema } from '../types';

/**
 * The application forms an advisor can open from a customer's profile, grouped by
 * product line and listed in the order they should appear: Life, General, Health,
 * and within a line by product.
 *
 * `file` is the form's file name in the knowledge base (`forms/`). It is the same
 * set the assistant can open through `get_form_schema`, so a form opened here and
 * one opened by the assistant are identical. Keep this list in sync with
 * lambda/formschema/index.py.
 */
export interface ApplicationFormEntry {
  line: ProductLine;
  /** The product type the form belongs to (matches `product_type` in the schema). */
  productType: string;
  /** Display name. Product names are brand names and are not translated. */
  name: string;
  file: string;
}

export const APPLICATION_FORMS: readonly ApplicationFormEntry[] = [
  { line: 'life', productType: 'term_life', name: 'Awesome Term Life', file: 'awesome-term-life-form.json' },
  { line: 'life', productType: 'whole_life', name: 'Awesome Whole Life', file: 'awesome-whole-life-form.json' },
  { line: 'life', productType: 'universal_life', name: 'Awesome Universal Life', file: 'awesome-universal-life-form.json' },
  { line: 'life', productType: 'variable_life', name: 'Awesome Variable Life', file: 'awesome-variable-life-form.json' },
  { line: 'general', productType: 'auto', name: 'Awesome Motor', file: 'awesome-motor-form.json' },
  { line: 'general', productType: 'home', name: 'Awesome Home Insurance', file: 'awesome-home-form.json' },
  { line: 'general', productType: 'travel', name: 'Awesome Travel', file: 'awesome-travel-form.json' },
  { line: 'health', productType: 'health', name: 'Awesome Health', file: 'awesome-health-form.json' },
  { line: 'health', productType: 'critical_illness', name: 'Awesome Critical Illness', file: 'awesome-critical-illness-form.json' },
  { line: 'health', productType: 'disability', name: 'Awesome Disability', file: 'awesome-disability-form.json' },
];

/** Fetch and check one form's schema. Throws with a readable message if it cannot be used. */
export async function loadApplicationForm(entry: ApplicationFormEntry): Promise<FormSchema> {
  const file = await loadDataFile('forms', entry.file);
  let parsed: unknown;
  try {
    parsed = JSON.parse(file.content);
  } catch {
    throw new Error(`${entry.name}: the form file is not valid JSON`);
  }
  const schema = parsed as Partial<FormSchema> | null;
  if (!schema || typeof schema.form_id !== 'string' || !Array.isArray(schema.sections)) {
    throw new Error(`${entry.name}: the form file is not a form`);
  }
  return schema as FormSchema;
}
