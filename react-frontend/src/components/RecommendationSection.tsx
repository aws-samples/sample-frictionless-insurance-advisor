import { ArrowRight, ShieldAlert, Sparkles, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { useRecommendation } from '../hooks/useRecommendation';
import type { GapLine } from '../lib/productLines';
import { Alert, Card, IconButton, Spinner } from '../ui';

interface RecommendationSectionProps {
  customerId: string;
  /** Which products the analysis covers. */
  line: GapLine;
  onClose: () => void;
}

/**
 * Coverage gap + Awesome product recommendation for one product line.
 *
 * Shown under the profile's action buttons once the advisor asks for it; it
 * does not load on its own. Triggers an async POST /recommend for the chosen
 * line (results are cached per customer, language and line). While loading,
 * shows a spinner; on success, lays out the structured payload as a
 * compact stack of cards. Errors are surfaced as a soft warning rather
 * than a destructive alert because the rest of the page is still useful.
 */
export function RecommendationSection({ customerId, line, onClose }: RecommendationSectionProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language ?? 'en';
  const { recommendation, loading, error } = useRecommendation(customerId, locale, line);

  return (
    <Card feature className="overflow-hidden">
      <div className="flex items-center gap-2 border-b border-border bg-gradient-to-r from-brand-2/10 via-brand-1/5 to-transparent px-5 py-3">
        <Sparkles className="h-4 w-4 text-brand-2" />
        <h3 className="flex-1 text-sm font-semibold uppercase tracking-wide">
          {t('assistant.customer.actions.gapAnalysis.resultTitle', { line: t(`domain.productLine.${line}`) })}
        </h3>
        <IconButton
          aria-label={t('assistant.customer.actions.gapAnalysis.close')}
          title={t('assistant.customer.actions.gapAnalysis.close')}
          onClick={onClose}
        >
          <X className="h-4 w-4" />
        </IconButton>
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

                        {g.financial_exposure?.estimated_cost ? (
                          <div className="mt-3 rounded-md border border-warning/30 bg-warning/5 px-3 py-2">
                            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                              <span className="text-[10px] font-semibold uppercase tracking-wide text-warning">
                                {t('assistant.recommendation.exposureLabel')}
                              </span>
                              <span className="text-sm font-semibold tabular-nums">
                                {g.financial_exposure.estimated_cost}
                              </span>
                              <span className="text-[10px] italic text-foreground-muted">
                                {t('assistant.recommendation.exposureIllustrative')}
                              </span>
                            </div>
                            {g.financial_exposure.scenario ? (
                              <p className="mt-1 text-xs text-foreground">
                                {g.financial_exposure.scenario}
                              </p>
                            ) : null}
                            {g.financial_exposure.basis ? (
                              <p className="mt-0.5 text-xs text-foreground-muted">
                                {g.financial_exposure.basis}
                              </p>
                            ) : null}
                          </div>
                        ) : null}

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
