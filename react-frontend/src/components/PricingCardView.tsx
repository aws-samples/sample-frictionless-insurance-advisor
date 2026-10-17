import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { fmtDate } from '../lib/format';
import { Badge, Card } from '../ui';

/*
 * Readable "price card" for the rate files in Data Explorer's Pricing Tables
 * folder. The files are the same ones the estimate_premium tool prices from,
 * so the card shows exactly what the assistants use: what the price depends
 * on, the base rates, the rating factors, the add-ons and worked examples.
 *
 * The files are data and may change shape, so everything is read defensively.
 */

interface InputSpec {
  type?: string;
  description?: string;
  choices?: string[];
  min?: number;
  max?: number;
  default?: unknown;
  required?: boolean;
  required_when?: Record<string, string>;
  source?: string;
}
interface Band {
  min?: number;
  max?: number;
  mult?: number;
}
interface Factor {
  input: string;
  label?: string;
  map?: Record<string, number>;
  bands?: Band[];
  when?: Record<string, string>;
}
interface Addon {
  input: string;
  label?: string;
  pct?: number;
  flat?: number;
  flat_per_unit?: { input: string; per: number; amount: number };
  rate_on_input?: number;
}
interface Example {
  label: string;
  options: Record<string, unknown>;
  annual_premium: number;
}
export interface PricingFile {
  product_type: string;
  product_name: string;
  currency?: string;
  effective_date?: string;
  premium_basis?: string;
  notes?: string[];
  inputs: Record<string, InputSpec>;
  base: Record<string, unknown> & { type: string; label?: string };
  factors?: Factor[];
  addons?: Addon[];
  frequency_loading?: Record<string, number>;
  examples?: Example[];
}
export interface AffordabilityFile {
  rule: string;
  cap_percent_of_annual_income: number;
  description?: string;
  applies_to?: string;
  if_income_missing?: string;
}

/** The rate file for one product, or null if this JSON is something else. */
export function parsePricingFile(content: string): PricingFile | null {
  try {
    const p: unknown = JSON.parse(content);
    if (
      p && typeof p === 'object' &&
      typeof (p as PricingFile).product_type === 'string' &&
      typeof (p as PricingFile).inputs === 'object' &&
      typeof (p as PricingFile).base === 'object' && (p as PricingFile).base !== null
    ) {
      return p as PricingFile;
    }
  } catch {
    /* not JSON */
  }
  return null;
}

/** The affordability rule file, or null. */
export function parseAffordabilityFile(content: string): AffordabilityFile | null {
  try {
    const p: unknown = JSON.parse(content);
    if (
      p && typeof p === 'object' &&
      typeof (p as AffordabilityFile).rule === 'string' &&
      typeof (p as AffordabilityFile).cap_percent_of_annual_income === 'number'
    ) {
      return p as AffordabilityFile;
    }
  } catch {
    /* not JSON */
  }
  return null;
}

const money = (n: number, digits = 0) =>
  `$${n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;

function pretty(value: string): string {
  if (/^\d{4,}$/.test(value)) return Number(value).toLocaleString('en-US');
  return value.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
}

function multText(m: number): string {
  return `× ${m.toLocaleString('en-US', { maximumFractionDigits: 3 })}`;
}

function bandLabel(b: { min?: number; max?: number }): string {
  if (b.min !== undefined && b.max !== undefined) return `${b.min} – ${b.max}`;
  if (b.min !== undefined) return `${b.min}+`;
  if (b.max !== undefined) return `≤ ${b.max}`;
  return '—';
}

function SectionTitle({ children }: { children: ReactNode }) {
  return <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide">{children}</h3>;
}

function Table({ head, rows, caption }: { head: string[]; rows: ReactNode[][]; caption?: string }) {
  return (
    <div>
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-border bg-background-muted/60">
              {head.map((h, i) => (
                <th key={`${h}-${i}`} className={`px-3 py-2 text-left font-medium ${i > 0 ? 'border-l border-border' : ''}`}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, ri) => (
              <tr key={ri} className={`border-b border-border last:border-0 ${ri % 2 === 1 ? 'bg-background-muted/30' : ''}`}>
                {r.map((c, ci) => (
                  <td key={ci} className={`px-3 py-2 align-top ${ci === 0 ? 'font-medium' : 'border-l border-border'}`}>
                    {c}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {caption ? <p className="mt-1 text-[11px] text-foreground-muted">{caption}</p> : null}
    </div>
  );
}

function allowedText(spec: InputSpec): string {
  if (spec.type === 'choice' && spec.choices) return spec.choices.map(pretty).join(', ');
  if (spec.type === 'boolean') return 'Yes / No';
  if (spec.type === 'number') {
    const lo = spec.min !== undefined ? spec.min.toLocaleString('en-US') : '';
    const hi = spec.max !== undefined ? spec.max.toLocaleString('en-US') : '';
    return lo && hi ? `${lo} – ${hi}` : lo || hi || 'Number';
  }
  return '';
}

function BaseRates({ base }: { base: PricingFile['base'] }) {
  const { t } = useTranslation();
  const bands = (base.age_bands as Band[] | undefined) ?? [];
  const caption = typeof base.label === 'string' ? base.label : undefined;
  switch (base.type) {
    case 'age_rate': {
      const rates = (base.rates as number[]) ?? [];
      const unit = Number(base.unit ?? 1000);
      return (
        <Table
          head={[t('assistant.pages.data.pricing.ageBand'), t('assistant.pages.data.pricing.ratePerUnit', { unit: money(unit) })]}
          rows={bands.map((b, i) => [bandLabel(b), money(rates[i] ?? 0, 2)])}
          caption={caption}
        />
      );
    }
    case 'age_column': {
      const columns = (base.columns as Record<string, number[]>) ?? {};
      const names = Object.keys(columns);
      const per = base.per as number | undefined;
      return (
        <Table
          head={[t('assistant.pages.data.pricing.ageBand'), ...names.map(pretty)]}
          rows={bands.map((b, i) => [bandLabel(b), ...names.map((n) => money(columns[n][i] ?? 0))])}
          caption={[caption, per ? t('assistant.pages.data.pricing.scaledNote', { amount: money(per) }) : ''].filter(Boolean).join(' ')}
        />
      );
    }
    case 'age_amount': {
      const rows = (base.rows as Record<string, number[]>) ?? {};
      const amounts = Object.keys(rows).sort((a, b) => Number(a) - Number(b));
      return (
        <Table
          head={[t('assistant.pages.data.pricing.monthlyBenefit'), ...bands.map(bandLabel)]}
          rows={amounts.map((a) => [money(Number(a)), ...bands.map((_, i) => money(rows[a][i] ?? 0))])}
          caption={[caption, t('assistant.pages.data.pricing.interpolatedNote')].filter(Boolean).join(' ')}
        />
      );
    }
    case 'rate_by_choice': {
      const rates = (base.rates as Record<string, number>) ?? {};
      const unit = Number(base.unit ?? 1000);
      return (
        <Table
          head={[pretty(String(base.choice_input ?? '')), t('assistant.pages.data.pricing.ratePerUnit', { unit: money(unit) })]}
          rows={Object.entries(rates).map(([k, v]) => [pretty(k), money(v, 2)])}
          caption={caption}
        />
      );
    }
    case 'flat_by_choice': {
      const values = (base.values as Record<string, number>) ?? {};
      return (
        <Table
          head={[pretty(String(base.choice_input ?? '')), t('assistant.pages.data.pricing.baseAnnual')]}
          rows={Object.entries(values).map(([k, v]) => [pretty(k), money(v)])}
          caption={caption}
        />
      );
    }
    case 'trip': {
      const perDay = (base.per_day as Record<string, number>) ?? {};
      const annual = (base.annual as Record<string, number>) ?? {};
      return (
        <Table
          head={[t('assistant.pages.data.pricing.geography'), t('assistant.pages.data.pricing.perDay'), t('assistant.pages.data.pricing.annualMulti')]}
          rows={Object.keys(perDay).map((g) => [pretty(g), money(perDay[g], 2), money(annual[g] ?? 0)])}
          caption={caption}
        />
      );
    }
    default:
      return null;
  }
}

function addonEffect(a: Addon, t: (k: string, o?: Record<string, unknown>) => string): string {
  if (a.pct !== undefined) return t('assistant.pages.data.pricing.addonPct', { pct: (a.pct * 100).toLocaleString('en-US', { maximumFractionDigits: 1 }) });
  if (a.flat !== undefined) return t('assistant.pages.data.pricing.addonFlat', { amount: money(a.flat) });
  if (a.flat_per_unit) return t('assistant.pages.data.pricing.addonPerUnit', { amount: money(a.flat_per_unit.amount), per: money(a.flat_per_unit.per) });
  if (a.rate_on_input !== undefined) return t('assistant.pages.data.pricing.addonOnAmount', { pct: (a.rate_on_input * 100).toLocaleString('en-US', { maximumFractionDigits: 2 }) });
  return '—';
}

function optionsText(options: Record<string, unknown>): string {
  return Object.entries(options)
    .map(([k, v]) => `${pretty(k)}: ${typeof v === 'boolean' ? (v ? 'yes' : 'no') : typeof v === 'number' && v >= 1000 ? money(v) : /^\d{4,}$/.test(String(v)) ? Number(v).toLocaleString('en-US') : String(v)}`)
    .join(' · ');
}

export function PricingCardView({ data }: { data: PricingFile }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;
  const inputs = Object.entries(data.inputs);
  const factors = (data.factors ?? []).filter((f) => f.map || f.bands);
  const addons = data.addons ?? [];
  const examples = data.examples ?? [];
  const loading = data.frequency_loading ? Object.entries(data.frequency_loading) : [];

  return (
    <div className="flex flex-col gap-5">
      <Card feature className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <h2 className="text-xl font-bold tracking-tight">
            <span className="text-brand-gradient">{data.product_name}</span>
          </h2>
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant="warning">{t('assistant.pages.data.pricing.indicative')}</Badge>
            {data.effective_date ? (
              <Badge variant="info">{t('assistant.pages.data.pricing.effective', { date: fmtDate(data.effective_date, locale) })}</Badge>
            ) : null}
          </div>
        </div>
        <p className="mt-1 text-xs text-foreground-muted">
          {t('assistant.pages.data.pricing.basis', { basis: data.premium_basis ?? '', currency: data.currency ?? 'USD' })}
        </p>
        {data.notes && data.notes.length > 0 ? (
          <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-foreground-muted">
            {data.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        ) : null}
      </Card>

      <section>
        <SectionTitle>{t('assistant.pages.data.pricing.dependsOn')}</SectionTitle>
        <Table
          head={[
            t('assistant.pages.data.pricing.option'),
            t('assistant.pages.data.pricing.meaning'),
            t('assistant.pages.data.pricing.allowed'),
            t('assistant.pages.data.pricing.defaultCol'),
          ]}
          rows={inputs.map(([name, spec]) => [
            <span key={name} className="font-mono text-xs">{name}</span>,
            <span key="d">
              {spec.description}
              {spec.source === 'profile' ? (
                <span className="ml-1 text-[11px] text-foreground-muted">({t('assistant.pages.data.pricing.fromProfile')})</span>
              ) : null}
            </span>,
            allowedText(spec),
            spec.required || spec.required_when
              ? t('assistant.pages.data.pricing.required')
              : spec.default !== undefined
                ? typeof spec.default === 'boolean'
                  ? spec.default ? 'Yes' : 'No'
                  : pretty(String(spec.default))
                : '—',
          ])}
        />
      </section>

      <section>
        <SectionTitle>{t('assistant.pages.data.pricing.baseRates')}</SectionTitle>
        <BaseRates base={data.base} />
      </section>

      {factors.length > 0 ? (
        <section>
          <SectionTitle>{t('assistant.pages.data.pricing.factors')}</SectionTitle>
          <p className="mb-2 text-xs text-foreground-muted">{t('assistant.pages.data.pricing.factorsHelp')}</p>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {factors.map((f) => (
              <Card key={f.input} className="p-3">
                <div className="mb-1.5 text-xs font-semibold">
                  {f.label ?? pretty(f.input)}
                  {f.when ? (
                    <span className="ml-1 font-normal text-foreground-muted">
                      ({Object.entries(f.when).map(([k, v]) => `${pretty(k)} = ${pretty(String(v))}`).join(', ')})
                    </span>
                  ) : null}
                </div>
                <table className="w-full text-xs">
                  <tbody>
                    {f.map
                      ? Object.entries(f.map).map(([k, v]) => (
                          <tr key={k} className="border-t border-border first:border-0">
                            <td className="py-1 pr-2">{k === 'true' ? 'Yes' : k === 'false' ? 'No' : pretty(k)}</td>
                            <td className="py-1 text-right tabular-nums">{multText(v)}</td>
                          </tr>
                        ))
                      : (f.bands ?? []).map((b, i) => (
                          <tr key={i} className="border-t border-border first:border-0">
                            <td className="py-1 pr-2">{bandLabel(b)}</td>
                            <td className="py-1 text-right tabular-nums">{multText(b.mult ?? 1)}</td>
                          </tr>
                        ))}
                  </tbody>
                </table>
              </Card>
            ))}
          </div>
        </section>
      ) : null}

      {addons.length > 0 ? (
        <section>
          <SectionTitle>{t('assistant.pages.data.pricing.addons')}</SectionTitle>
          <Table
            head={[t('assistant.pages.data.pricing.addon'), t('assistant.pages.data.pricing.effect')]}
            rows={addons.map((a) => [a.label ?? pretty(a.input), addonEffect(a, t as never)])}
          />
        </section>
      ) : null}

      {loading.length > 0 ? (
        <section>
          <SectionTitle>{t('assistant.pages.data.pricing.frequency')}</SectionTitle>
          <Table
            head={[t('assistant.pages.data.pricing.paidEvery'), t('assistant.pages.data.pricing.loading')]}
            rows={loading.map(([k, v]) => [pretty(k), v === 1 ? t('assistant.pages.data.pricing.noLoading') : `+${((v - 1) * 100).toFixed(0)}%`])}
          />
        </section>
      ) : null}

      {examples.length > 0 ? (
        <section>
          <SectionTitle>{t('assistant.pages.data.pricing.examples')}</SectionTitle>
          <Table
            head={[t('assistant.pages.data.pricing.example'), t('assistant.pages.data.pricing.choices'), t('assistant.pages.data.pricing.annualPremium')]}
            rows={examples.map((e) => [
              e.label,
              <span key="o" className="text-xs text-foreground-muted">{optionsText(e.options)}</span>,
              <span key="p" className="font-semibold tabular-nums">{money(e.annual_premium)}</span>,
            ])}
            caption={t('assistant.pages.data.pricing.examplesNote')}
          />
        </section>
      ) : null}
    </div>
  );
}

export function AffordabilityCardView({ data }: { data: AffordabilityFile }) {
  const { t } = useTranslation();
  return (
    <Card feature className="p-5">
      <h2 className="text-xl font-bold tracking-tight">
        <span className="text-brand-gradient">{t('assistant.pages.data.pricing.affordabilityTitle')}</span>
      </h2>
      <div className="mt-3 flex items-baseline gap-2">
        <span className="text-4xl font-extrabold tabular-nums text-brand-2">{data.cap_percent_of_annual_income}%</span>
        <span className="text-sm text-foreground-muted">{t('assistant.pages.data.pricing.ofIncome')}</span>
      </div>
      {data.description ? <p className="mt-3 text-sm">{data.description}</p> : null}
      {data.if_income_missing ? (
        <p className="mt-2 text-xs text-foreground-muted">{data.if_income_missing}</p>
      ) : null}
      <p className="mt-3 text-[11px] text-foreground-muted">{data.rule}</p>
    </Card>
  );
}
