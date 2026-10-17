/**
 * Product lines for the Comparator's Life / General / Health switch.
 *
 * The catalog names each product type in English ("Term Life Insurance"). This file
 * says which line each type belongs to, and the order the types appear in within a
 * line. It is the single place to change if a product is classified differently:
 *
 *   life     the life products
 *   general  property and casualty: motor, home, travel (and renters, a competitor-only type)
 *   health   health, critical illness and disability
 *
 * A type the catalog gains later that is not listed here shows under General rather
 * than disappearing.
 */
export type ProductLine = 'life' | 'general' | 'health';

export const PRODUCT_LINES: readonly ProductLine[] = ['life', 'general', 'health'];

/** What a gap analysis can be scoped to: one line, or everything. */
export type GapLine = ProductLine | 'all';

export const GAP_LINES: readonly GapLine[] = ['life', 'general', 'health', 'all'];

const TYPES_BY_LINE: Record<ProductLine, readonly string[]> = {
  life: [
    'Term Life Insurance',
    'Whole Life Insurance',
    'Universal Life Insurance',
    'Variable Life Insurance',
  ],
  general: ['Auto Insurance', 'Home Insurance', 'Travel Insurance', 'Renters Insurance'],
  health: ['Health Insurance', 'Critical Illness Insurance', 'Disability Insurance'],
};

const KNOWN_TYPES = new Set<string>(Object.values(TYPES_BY_LINE).flat());

/**
 * The catalog's product types that belong to one line, in display order.
 * `available` is what the catalog returned (alphabetical); only those are listed.
 */
export function typesForLine(available: readonly string[], line: ProductLine): string[] {
  const present = new Set(available);
  const listed = TYPES_BY_LINE[line].filter((type) => present.has(type));
  if (line !== 'general') return listed;
  const unlisted = available.filter((type) => !KNOWN_TYPES.has(type)).sort();
  return [...listed, ...unlisted];
}
