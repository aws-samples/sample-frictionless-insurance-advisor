import { useTranslation } from 'react-i18next';
import { fmtDateTime } from '../lib/format';
import type { SavedApplication } from '../types';
import { Alert, Badge, Card } from '../ui';

/*
 * Read-only view of one saved application (lambda/applications). The data is JSON from
 * storage, so it is read defensively.
 */

/** The application in a Data-tab payload, or null if this JSON is something else. */
export function parseApplication(content: string): SavedApplication | null {
  try {
    const p: unknown = JSON.parse(content);
    if (p && typeof p === 'object') {
      const a = p as Partial<SavedApplication>;
      if (typeof a.ref === 'string' && Array.isArray(a.sections) && a.answers && typeof a.answers === 'object') {
        return { ...(a as SavedApplication), omitted: Array.isArray(a.omitted) ? a.omitted : [] };
      }
    }
  } catch {
    /* not JSON */
  }
  return null;
}

const ORIGIN_VARIANT = { profile: 'brand', voice: 'success', manual: 'neutral' } as const;

export function ApplicationView({ data }: { data: SavedApplication }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;
  const total = data.sections.reduce((n, s) => n + s.fields.length, 0);
  const hidden = data.sections.reduce((n, s) => n + s.fields.filter((f) => f.visible === false).length, 0);
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <Card feature className="p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="success" dot>
            {t('assistant.pages.data.application.status')}
          </Badge>
          <span className="font-mono text-xs">{data.ref}</span>
          <span className="text-xs text-foreground-muted">{fmtDateTime(data.saved_at, locale)}</span>
          {data.revision && data.revision > 1 ? (
            <Badge variant="neutral">
              {t('assistant.pages.data.application.revision', {
                revision: data.revision,
                time: fmtDateTime(data.updated_at ?? data.saved_at, locale),
              })}
            </Badge>
          ) : null}
        </div>
        <h4 className="mt-2 text-base font-semibold">{data.form_title ?? data.product_name ?? data.product_type}</h4>
        <p className="mt-0.5 text-xs text-foreground-muted">
          {data.customer
            ? t('assistant.pages.data.application.forCustomer', { name: data.customer.name ?? data.customer.customer_id })
            : t('assistant.pages.data.application.forProspect')}
          {' · '}
          {t('assistant.pages.data.application.answerCount', { count: total })}
          {hidden > 0 ? ` · ${t('assistant.pages.data.application.hiddenCount', { count: hidden })}` : ''}
        </p>
        <p className="mt-2 text-xs text-foreground-muted">{t('assistant.pages.data.application.expires')}</p>
      </Card>
      {data.omitted.length > 0 ? (
        <Alert variant="info">
          {t('assistant.pages.data.application.omitted', { fields: data.omitted.map((o) => o.label).join(', ') })}
        </Alert>
      ) : null}
      {data.sections.map((section) => (
        <section key={section.id}>
          <h4 className="mb-2 text-sm font-semibold uppercase tracking-wide">{section.title}</h4>
          <Card className="p-4">
            <dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
              {section.fields.map((f) => (
                <div key={f.path} className={f.visible === false ? 'opacity-60' : undefined}>
                  <dt className="flex flex-wrap items-center gap-1.5 text-[11px] uppercase tracking-wide text-foreground-muted">
                    {f.label}
                    <Badge variant={ORIGIN_VARIANT[f.origin] ?? 'neutral'}>
                      {t(`assistant.pages.data.application.origin.${f.origin}`, { defaultValue: f.origin })}
                    </Badge>
                    {f.visible === false ? (
                      <Badge variant="warning">{t('assistant.pages.data.application.hidden')}</Badge>
                    ) : null}
                  </dt>
                  <dd className="mt-0.5 break-words text-sm font-medium">{f.display || f.value}</dd>
                </div>
              ))}
            </dl>
          </Card>
        </section>
      ))}
    </div>
  );
}
