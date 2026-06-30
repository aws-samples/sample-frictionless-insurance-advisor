import { useMemo } from 'react';
import {
  Briefcase,
  Building2,
  CalendarDays,
  Hash,
  Mail,
  MapPin,
  Phone,
  Shield,
  ShieldCheck,
  Sparkles,
  Users as UsersIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { fmtDate } from '../lib/format';
import { Avatar, Badge, Card } from '../ui';
import type { Customer } from '../types';

import { PoliciesList } from './PoliciesList';
import { RecommendationSection } from './RecommendationSection';

interface CustomerPanelProps {
  customer: Customer | null;
  totalCustomers: number;
  totalPolicies: number;
}

interface InfoRowProps {
  icon: React.ReactNode;
  label: string;
  value: string | number | null | undefined;
}

function InfoRow({ icon, label, value }: InfoRowProps) {
  if (!value && value !== 0) return null;
  return (
    <div className="flex items-start gap-2.5 text-sm">
      <span className="mt-0.5 text-foreground-muted shrink-0">{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="text-xs uppercase tracking-wide text-foreground-muted">{label}</div>
        <div className="mt-0.5 text-foreground break-words">{value}</div>
      </div>
    </div>
  );
}

export function CustomerPanel({ customer, totalCustomers, totalPolicies }: CustomerPanelProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;

  // Pre-compute partition before the early return — Rules of Hooks.
  const policies = customer?.policies ?? [];
  const { unicornPolicies, thirdPartyPolicies } = useMemo(() => {
    const unicorn: typeof policies = [];
    const thirdParty: typeof policies = [];
    for (const p of policies) {
      if (p.third_party) thirdParty.push(p);
      else unicorn.push(p);
    }
    return { unicornPolicies: unicorn, thirdPartyPolicies: thirdParty };
  }, [policies]);

  if (!customer) {
    return (
      <div className="p-6 sm:p-8">
        <div className="flex items-start gap-4">
          <Sparkles className="mt-1 h-6 w-6 text-brand-2" />
          <div>
            <h2 className="text-2xl font-bold tracking-tight">
              <span className="text-brand-gradient">{t('assistant.welcome.heading')}</span>
            </h2>
            <p className="mt-1 max-w-prose text-sm text-foreground-muted">
              {t('assistant.welcome.body')}
            </p>
          </div>
        </div>

        <div className="mt-8 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Card feature className="p-5">
            <div className="text-xs font-medium uppercase tracking-wide text-foreground-muted">
              {t('assistant.welcome.totalCustomers')}
            </div>
            <div className="mt-2 text-3xl font-bold tabular-nums">
              <span className="text-brand-gradient">{totalCustomers}</span>
            </div>
          </Card>
          <Card feature className="p-5">
            <div className="text-xs font-medium uppercase tracking-wide text-foreground-muted">
              {t('assistant.welcome.activePolicies')}
            </div>
            <div className="mt-2 text-3xl font-bold tabular-nums">
              <span className="text-brand-gradient">{totalPolicies}</span>
            </div>
          </Card>
        </div>
      </div>
    );
  }

  const isProspect = customer.policies.every((p) => p.third_party);
  const badgeVariant = isProspect
    ? 'info'
    : customer.status === 'Active'
      ? 'success'
      : 'warning';
  const statusKey = isProspect ? 'Prospect' : customer.status;
  const badgeText = t(`domain.customerStatus.${statusKey}`, { defaultValue: statusKey });

  const maritalStatusLabel = customer.marital_status
    ? t(`domain.maritalStatus.${customer.marital_status}`, { defaultValue: customer.marital_status })
    : null;

  return (
    <div className="space-y-6 p-6 sm:p-8">
      {/* Header */}
      <Card feature className="p-5 sm:p-6">
        <div className="flex items-start gap-4">
          <Avatar name={customer.name} size="lg" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="truncate text-xl font-bold tracking-tight">{customer.name}</h2>
              <Badge variant={badgeVariant} dot>
                {badgeText}
              </Badge>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-foreground-muted">
              <span className="inline-flex items-center gap-1">
                <Hash className="h-3 w-3" />
                <span className="font-mono">{customer.customer_id}</span>
              </span>
              <span className="inline-flex items-center gap-1">
                <CalendarDays className="h-3 w-3" />
                {t('assistant.customer.fields.joinDate')}: {fmtDate(customer.join_date, locale)}
              </span>
            </div>
          </div>
        </div>

        {/* Info grid */}
        <div className="mt-6 grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
          <InfoRow
            icon={<Mail className="h-4 w-4" />}
            label={t('assistant.customer.fields.email')}
            value={customer.email}
          />
          <InfoRow
            icon={<Phone className="h-4 w-4" />}
            label={t('assistant.customer.fields.phone')}
            value={customer.phone}
          />
          <InfoRow
            icon={<MapPin className="h-4 w-4" />}
            label={t('assistant.customer.fields.address')}
            value={customer.address}
          />
          <InfoRow
            icon={<Briefcase className="h-4 w-4" />}
            label={t('assistant.customer.fields.occupation')}
            value={customer.occupation}
          />
          <InfoRow
            icon={<UsersIcon className="h-4 w-4" />}
            label={t('assistant.customer.fields.maritalStatus')}
            value={maritalStatusLabel}
          />
          <InfoRow
            icon={<Building2 className="h-4 w-4" />}
            label={t('assistant.customer.fields.policiesCount')}
            value={customer.policies.length}
          />
        </div>
      </Card>

      {/* Recommendation */}
      <RecommendationSection customerId={customer.customer_id} />

      {/* Unicorn policies */}
      <section>
        <header className="mb-3 flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-brand-2" />
          <h3 className="text-sm font-semibold uppercase tracking-wide">
            {t('assistant.customer.policiesSection')}
          </h3>
        </header>
        <PoliciesList policies={unicornPolicies} />
      </section>

      {/* Third-party policies — only when present */}
      {thirdPartyPolicies.length > 0 ? (
        <section>
          <header className="mb-3 flex items-center gap-2">
            <Shield className="h-4 w-4 text-foreground-muted" />
            <h3 className="text-sm font-semibold uppercase tracking-wide">
              {t('assistant.customer.thirdPartyPoliciesSection')}
            </h3>
          </header>
          <PoliciesList policies={thirdPartyPolicies} showInsurer />
        </section>
      ) : null}
    </div>
  );
}
