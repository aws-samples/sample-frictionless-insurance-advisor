import { useTranslation } from 'react-i18next';

import { fmtDate } from '../lib/format';
import { Badge, Card } from '../ui';

/*
 * Readable view of competitors/carrier_ratings.json in Data Explorer: one row
 * per carrier, one column per rating agency. The same file feeds the
 * Comparator's "Carrier financial strength" row, so what is shown here is what
 * the comparison and the customer PDF use.
 *
 * The file is data and may change shape, so everything is read defensively.
 */
interface Rating {
  agency?: string;
  rating?: string;
  label?: string;
  outlook?: string;
}
interface Carrier {
  carrier_id?: string;
  carrier_name?: string;
  illustrative?: boolean;
  ratings?: Rating[];
}
export interface CarrierRatingsFile {
  description?: string;
  as_of?: string;
  carriers: Carrier[];
}

/** The ratings file, or null if this JSON is something else. */
export function parseCarrierRatingsFile(content: string): CarrierRatingsFile | null {
  try {
    const p: unknown = JSON.parse(content);
    if (!p || typeof p !== 'object') return null;
    const carriers = (p as CarrierRatingsFile).carriers;
    if (!Array.isArray(carriers) || carriers.length === 0) return null;
    const looksRight = carriers.every(
      (c) =>
        c &&
        typeof c === 'object' &&
        typeof c.carrier_name === 'string' &&
        Array.isArray(c.ratings) &&
        c.ratings.every((r) => r && typeof r.agency === 'string')
    );
    return looksRight ? (p as CarrierRatingsFile) : null;
  } catch {
    /* not JSON */
  }
  return null;
}

/** AM Best, S&P and Moody's first, in that order; any other agency follows. */
const PREFERRED = ['AM Best', 'S&P Global Ratings', "Moody's"];
const norm = (s: string) => s.trim().toLowerCase().replace(/[^a-z0-9]/g, '');

export function CarrierRatingsView({ data }: { data: CarrierRatingsFile }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;

  const names = new Map<string, string>();
  for (const c of data.carriers) {
    for (const r of c.ratings ?? []) {
      if (r.agency && !names.has(norm(r.agency))) names.set(norm(r.agency), r.agency);
    }
  }
  const agencies = [
    ...PREFERRED.filter((a) => names.has(norm(a))).map((a) => names.get(norm(a)) as string),
    ...[...names.entries()].filter(([k]) => !PREFERRED.some((a) => norm(a) === k)).map(([, v]) => v),
  ];
  const anyIllustrative = data.carriers.some((c) => c.illustrative);

  return (
    <div className="flex flex-col gap-4">
      <Card feature className="p-5">
        <h2 className="text-xl font-bold tracking-tight">
          <span className="text-brand-gradient">{t('assistant.pages.comparator.ratings.title')}</span>
        </h2>
        <div className="mt-3 flex flex-wrap gap-2">
          <Badge variant="neutral">
            {t('assistant.pages.data.ratings.carrierCount', { count: data.carriers.length })}
          </Badge>
          <Badge variant="neutral">
            {t('assistant.pages.data.ratings.agencyCount', { count: agencies.length })}
          </Badge>
          {data.as_of ? (
            <Badge variant="info">
              {t('assistant.pages.comparator.ratings.asOf', { date: fmtDate(data.as_of, locale) })}
            </Badge>
          ) : null}
        </div>
      </Card>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] border-collapse text-left text-xs">
            <thead>
              <tr className="border-b border-border bg-background-muted/40">
                <th scope="col" className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-foreground-muted">
                  {t('assistant.pages.data.ratings.carrier')}
                </th>
                {agencies.map((a) => (
                  <th
                    key={a}
                    scope="col"
                    className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-foreground-muted"
                  >
                    {a}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.carriers.map((c, i) => (
                <tr key={c.carrier_id ?? `${c.carrier_name}-${i}`} className="border-b border-border last:border-b-0 align-top">
                  <th scope="row" className="px-4 py-3 font-semibold text-foreground">
                    <span className="block">{c.carrier_name}</span>
                    {c.illustrative ? (
                      <Badge variant="warning" className="mt-1">
                        {t('assistant.pages.data.ratings.illustrative')}
                      </Badge>
                    ) : null}
                  </th>
                  {agencies.map((a) => {
                    const r = (c.ratings ?? []).find((x) => x.agency && norm(x.agency) === norm(a));
                    const detail = r
                      ? [r.label, r.outlook ? `${r.outlook} ${t('assistant.pages.comparator.ratings.outlook')}` : '']
                          .filter(Boolean)
                          .join(' · ')
                      : '';
                    return (
                      <td key={a} className="px-4 py-3">
                        {r ? (
                          <>
                            <span className="block text-sm font-semibold tabular-nums text-foreground">{r.rating}</span>
                            {detail ? <span className="block text-[11px] text-foreground-muted">{detail}</span> : null}
                          </>
                        ) : (
                          <span className="text-foreground-muted">{t('assistant.pages.comparator.ratings.notStated')}</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {anyIllustrative ? (
        <p className="px-1 text-[11px] text-foreground-muted">
          {t('assistant.pages.comparator.ratings.illustrativeNote')}
        </p>
      ) : null}
      {data.description ? <p className="px-1 text-[11px] text-foreground-muted">{data.description}</p> : null}
    </div>
  );
}
