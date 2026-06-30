import {
  Activity,
  Briefcase,
  Car,
  CalendarDays,
  HeartPulse,
  Home,
  Receipt,
  Shield,
  Sprout,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { toNumber } from '../lib/api';
import { fmtCurrency, fmtDate } from '../lib/format';
import { Badge, Card } from '../ui';
import type { Policy } from '../types';

import { PolicyDetails } from './PolicyDetails';

interface PoliciesListProps {
  policies: Policy[];
  /** When true, surface the insurer line on each card. Used for 3P. */
  showInsurer?: boolean;
  /** Custom empty-state message override; defaults to the prospect copy. */
  emptyMessage?: string;
}

function policyTypeIcon(type: string) {
  const t = type.toLowerCase();
  if (t.includes('auto')) return Car;
  if (t.includes('home') || t.includes('property')) return Home;
  if (t.includes('health')) return HeartPulse;
  if (t.includes('life')) return Sprout;
  if (t.includes('disability')) return Activity;
  return Shield;
}

export function PoliciesList({ policies, showInsurer, emptyMessage }: PoliciesListProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;

  if (policies.length === 0) {
    return (
      <Card className="p-6 text-center">
        <p className="text-sm italic text-foreground-muted">
          {emptyMessage ?? t('assistant.policies.emptyProspect')}
        </p>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {policies.map((policy) => {
        const Icon = policyTypeIcon(policy.type);
        const policyTypeLabel = t(`domain.policyType.${policy.type}`, { defaultValue: policy.type });
        const frequencyLabel = t(`domain.premiumFrequency.${policy.premium_frequency}`, {
          defaultValue: policy.premium_frequency,
        });
        const policyStatusLabel = t(`domain.policyStatus.${policy.status}`, {
          defaultValue: policy.status,
        });
        const statusVariant = policy.status === 'Active' ? 'success' : 'warning';

        return (
          <Card key={policy.id} className="p-4 sm:p-5 card-hover">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="flex min-w-0 items-start gap-3">
                <div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-brand-2/15 to-brand-1/10 text-brand-2 ring-1 ring-brand-2/20">
                  <Icon className="h-5 w-5" />
                </div>
                <div className="min-w-0">
                  <div className="font-semibold leading-tight">{policyTypeLabel}</div>
                  {policy.product_name ? (
                    <div className="mt-0.5 text-sm text-foreground-muted">{policy.product_name}</div>
                  ) : null}
                  {showInsurer && policy.insurer ? (
                    <div className="mt-1 inline-flex items-center gap-1.5 text-xs">
                      <Briefcase className="h-3 w-3 text-foreground-muted" />
                      <span className="text-foreground-muted">
                        {t('assistant.policies.insurer')}:
                      </span>
                      <span className="font-medium">{policy.insurer}</span>
                    </div>
                  ) : null}
                  <div className="mt-1 inline-flex items-center gap-1 text-xs text-foreground-muted">
                    <span>{t('assistant.policies.id')}:</span>
                    <span className="font-mono">{policy.id}</span>
                  </div>
                </div>
              </div>

              <div className="flex flex-col items-end gap-1.5 text-right">
                <div className="inline-flex items-center gap-1.5 text-base font-semibold">
                  <Receipt className="h-3.5 w-3.5 text-foreground-muted" />
                  <span className="tabular-nums">
                    {t('assistant.policies.perFrequency', {
                      amount: fmtCurrency(toNumber(policy.premium_amount), locale),
                      frequency: frequencyLabel,
                    })}
                  </span>
                </div>
                <div className="text-xs text-foreground-muted">
                  {t('assistant.policies.coverage')}:{' '}
                  <span className="font-medium text-foreground tabular-nums">
                    {fmtCurrency(toNumber(policy.coverage_amount), locale)}
                  </span>
                </div>
                <Badge variant={statusVariant} dot>
                  {policyStatusLabel}
                </Badge>
                <div className="inline-flex items-center gap-1 text-xs text-foreground-muted">
                  <CalendarDays className="h-3 w-3" />
                  {t('assistant.policies.renews')}: {fmtDate(policy.renewal_date, locale)}
                </div>
              </div>
            </div>

            <div className="mt-3 border-t border-border pt-3">
              <PolicyDetails policy={policy} />
            </div>
          </Card>
        );
      })}
    </div>
  );
}
