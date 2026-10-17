import { useMemo } from 'react';
import {
  AlertTriangle,
  Briefcase,
  Building2,
  CalendarDays,
  Hash,
  Mail,
  MapPin,
  Phone,
  Shield,
  ShieldCheck,
  Users as UsersIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { fmtDate } from '../lib/format';
import { Avatar, Badge, Card } from '../ui';
import type { Customer, CustomerNote, FormSchema, PriorityAction } from '../types';
import { Cockpit } from './Cockpit';

import { NotesCard } from './NotesCard';
import { PoliciesList } from './PoliciesList';
import { ProfileActions } from './ProfileActions';

// Stable reference for customers without notes, so NotesCard's resync effect
// only fires when the notes actually change.
const NO_NOTES: CustomerNote[] = [];

interface CustomerPanelProps {
  customer: Customer | null;
  totalCustomers: number;
  totalPolicies: number;
  /** Cockpit: open a customer and start a prompt. */
  onOpenCustomer?: (customerId: string, action: PriorityAction) => void;
  /** Opens an application form for the customer (the left panel switches to it). */
  onOpenForm: (productType: string, schema: FormSchema) => void;
}

// Data freshness: prompt the advisor to re-verify a client's details when the
// profile hasn't been reviewed recently, so the coverage-gap analysis isn't
// trusted against stale inputs. The threshold is 90 days.
const STALE_AFTER_DAYS = 90;

/** Whole days between an ISO date and now, or null if missing/invalid. */
function daysSince(iso: string | undefined): number | null {
  if (!iso) return null;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return null;
  return Math.floor((Date.now() - parsed.getTime()) / 86_400_000);
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

export function CustomerPanel({ customer, totalCustomers, totalPolicies, onOpenCustomer, onOpenForm }: CustomerPanelProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;

  // Pre-compute partition before the early return — Rules of Hooks.
  const policies = customer?.policies ?? [];
  const { awesomePolicies, thirdPartyPolicies } = useMemo(() => {
    const awesome: typeof policies = [];
    const thirdParty: typeof policies = [];
    for (const p of policies) {
      if (p.third_party) thirdParty.push(p);
      else awesome.push(p);
    }
    return { awesomePolicies: awesome, thirdPartyPolicies: thirdParty };
  }, [policies]);

  if (!customer) {
    return (
      <Cockpit
        totalCustomers={totalCustomers}
        totalPolicies={totalPolicies}
        onOpen={(id, action) => onOpenCustomer?.(id, action)}
      />
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

  const genderLabel = customer.gender
    ? t(`domain.gender.${customer.gender}`, { defaultValue: customer.gender })
    : null;
  const maritalStatusLabel = customer.marital_status
    ? t(`domain.maritalStatus.${customer.marital_status}`, { defaultValue: customer.marital_status })
    : null;

  // Fall back to join_date only if last_reviewed is absent (legacy records).
  const reviewAgeDays = daysSince(customer.last_reviewed ?? customer.join_date);
  const isStale = reviewAgeDays !== null && reviewAgeDays > STALE_AFTER_DAYS;

  return (
    <div className="space-y-6 p-6 sm:p-8">
      {/* Actions: gap analysis and application form. Keyed so a new customer starts clean. */}
      <ProfileActions key={customer.customer_id} customerId={customer.customer_id} onOpenForm={onOpenForm} />

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
              {isStale ? (
                <span
                  title={t('assistant.customer.dataFreshness.tooltip', { days: STALE_AFTER_DAYS })}
                  className="inline-flex"
                >
                  <Badge variant="warning">
                    <AlertTriangle className="mr-1 h-3 w-3" />
                    {t('assistant.customer.dataFreshness.badge')}
                  </Badge>
                </span>
              ) : null}
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
            label={t('assistant.customer.fields.gender')}
            value={genderLabel}
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

      {/* Notes: life events and interactions */}
      <NotesCard customerId={customer.customer_id} notes={customer.notes ?? NO_NOTES} />

      {/* Awesome policies */}
      <section>
        <header className="mb-3 flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-brand-2" />
          <h3 className="text-sm font-semibold uppercase tracking-wide">
            {t('assistant.customer.policiesSection')}
          </h3>
        </header>
        <PoliciesList policies={awesomePolicies} />
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
