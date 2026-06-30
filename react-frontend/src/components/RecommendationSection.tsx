import { ArrowRight, ShieldAlert, Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { useRecommendation } from '../hooks/useRecommendation';
import { Alert, Card, Spinner } from '../ui';

interface RecommendationSectionProps {
  customerId: string;
}

/**
 * Auto-loaded coverage gap + Unicorn product recommendation block.
 *
 * Renders between the customer info and policies sections. Triggers an
 * async POST /recommend whenever the customer changes. While loading,
 * shows a spinner; on success, lays out the structured payload as a
 * compact stack of cards. Errors are surfaced as a soft warning rather
 * than a destructive alert because the rest of the page is still useful.
 */
export function RecommendationSection({ customerId }: RecommendationSectionProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language ?? 'en';
  const { recommendation, loading, error } = useRecommendation(customerId, locale);

  return (
    <Card feature className="overflow-hidden">
      <div className="flex items-center gap-2 border-b border-border bg-gradient-to-r from-brand-2/10 via-brand-1/5 to-transparent px-5 py-3">
        <Sparkles className="h-4 w-4 text-brand-2" />
        <h3 className="text-sm font-semibold uppercase tracking-wide">
          {t('assistant.customer.recommendationSection')}
        </h3>
      </div>

      <div className="p-5">
        {loading ? (
          <div className="flex items-center gap-3">
            <Spinner size="sm" />
            <span className="text-sm text-foreground-muted">
              {t('assistant.recommendation.loading')}
            </span>
          </div>
        ) : null}

        {error ? (
          <Alert variant="warning">
            {t('assistant.recommendation.error', { message: error })}
          </Alert>
        ) : null}

        {!loading && !error && recommendation ? (
          <div className="space-y-4">
            {recommendation.summary ? (
              <p className="text-sm leading-relaxed">{recommendation.summary}</p>
            ) : null}

            {recommendation.gaps.length === 0 ? (
              <p className="rounded-md bg-success/5 px-3 py-2 text-sm italic text-success">
                {t('assistant.recommendation.noGaps')}
              </p>
            ) : (
              <div className="space-y-3">
                {recommendation.gaps.map((g, idx) => (
                  <Card key={`${g.gap}-${idx}`} className="p-4">
                    <div className="flex items-start gap-3">
                      <div className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md bg-warning/10 text-warning ring-1 ring-warning/30">
                        <ShieldAlert className="h-4 w-4" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="font-semibold leading-tight">{g.gap}</div>
                        <p className="mt-1 text-sm text-foreground-muted">{g.why}</p>

                        {g.recommendations.length > 0 ? (
                          <div className="mt-3 space-y-2 border-l-2 border-brand-2/40 pl-3">
                            {g.recommendations.map((r, i) => (
                              <div key={`${r.product_name}-${i}`}>
                                <div className="inline-flex items-center gap-1.5 text-sm">
                                  <ArrowRight className="h-3.5 w-3.5 text-brand-2" />
                                  <span className="font-semibold">{r.product_name}</span>
                                  <span className="text-xs text-foreground-muted">
                                    ({r.product_type})
                                  </span>
                                </div>
                                <p className="ml-5 mt-0.5 text-xs text-foreground-muted">
                                  {r.why_helps}
                                </p>
                              </div>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    </div>
                  </Card>
                ))}
              </div>
            )}

            {recommendation.disclaimer ? (
              <p className="border-t border-border pt-3 text-xs italic text-foreground-muted">
                {recommendation.disclaimer}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>
    </Card>
  );
}
