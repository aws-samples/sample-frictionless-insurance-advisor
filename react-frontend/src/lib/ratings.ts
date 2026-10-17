import type { CarrierRating, ComparisonProduct } from '../types';

/** The agencies shown first, in this order. Any other agency a brochure names follows. */
const PREFERRED_AGENCIES = ['AM Best', 'S&P Global Ratings', "Moody's"];

export interface RatingRow {
  agency: string;
  /** One entry per product, same order as the comparison's products. */
  cells: (CarrierRating | null)[];
}

export interface RatingsSummary {
  rows: RatingRow[];
  /** At least one product has ratings to show. */
  hasAny: boolean;
  /** A competitor's ratings are demo data, not real figures. */
  anyIllustrative: boolean;
  /** A product's ratings come from an uploaded brochure and are unverified. */
  anyFromBrochure: boolean;
  asOf?: string;
}

function norm(agency: string): string {
  return agency.trim().toLowerCase().replace(/[^a-z0-9]/g, '');
}

/**
 * Lay the carriers' financial-strength ratings out as rows by agency, so the
 * comparison and the customer PDF can show the same "Carrier financial
 * strength" table. The ratings are attached by the backend, never by the model.
 */
export function summariseRatings(products: ComparisonProduct[]): RatingsSummary {
  const names = new Map<string, string>();
  for (const p of products) {
    for (const r of p.ratings ?? []) {
      if (!names.has(norm(r.agency))) names.set(norm(r.agency), r.agency);
    }
  }
  const ordered = [
    ...PREFERRED_AGENCIES.filter((a) => names.has(norm(a))).map((a) => norm(a)),
    ...[...names.keys()].filter((k) => !PREFERRED_AGENCIES.some((a) => norm(a) === k)),
  ];
  const rows: RatingRow[] = ordered.map((key) => ({
    agency: names.get(key) ?? key,
    cells: products.map((p) => (p.ratings ?? []).find((r) => norm(r.agency) === key) ?? null),
  }));
  return {
    rows,
    hasAny: rows.length > 0,
    anyIllustrative: products.some((p) => p.ratings_illustrative),
    anyFromBrochure: products.some((p) => p.ratings_source === 'brochure' && (p.ratings ?? []).length > 0),
    asOf: products.find((p) => p.ratings_as_of)?.ratings_as_of,
  };
}

/** "Superior · Stable outlook" style detail for a rating, or ''. */
export function ratingDetail(r: CarrierRating, outlookWord: string): string {
  return [r.label, r.outlook ? `${r.outlook} ${outlookWord}` : ''].filter(Boolean).join(' · ');
}
