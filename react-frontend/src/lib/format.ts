/**
 * Locale-aware formatting helpers backed by the Intl APIs.
 *
 * The active UI locale comes from i18next (`i18n.language`). Components
 * call these helpers with the current locale rather than reaching into
 * i18next directly, so the formatting layer stays decoupled from the i18n
 * library choice.
 */

export function fmtCurrency(
  amount: number,
  locale: string,
  currency: string = 'USD'
): string {
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency,
    maximumFractionDigits: 0,
  }).format(amount);
}

export function fmtNumber(value: number, locale: string): string {
  return new Intl.NumberFormat(locale).format(value);
}

/**
 * Format an ISO date string (YYYY-MM-DD) for display. Invalid or empty
 * inputs are returned as-is so formatting never throws on partial data.
 */
export function fmtDate(iso: string | undefined, locale: string): string {
  if (!iso) return '';
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium' }).format(parsed);
}
