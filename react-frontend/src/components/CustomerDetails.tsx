import { useId, useState } from 'react';
import {
  Baby,
  Briefcase,
  Cake,
  ChevronDown,
  Cigarette,
  Globe,
  Home,
  HeartPulse,
  MapPin,
  PiggyBank,
  User,
  Wallet,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { fmtCurrency, fmtDate } from '../lib/format';
import type { Customer } from '../types';
import { InfoRow } from './InfoRow';

/**
 * Collapsible "Show details" section of the customer profile card.
 *
 * Shows the profile fields the summary grid leaves out: name and address
 * parts, date of birth, citizenship, tobacco status, dependents, employment,
 * income, net worth band, home ownership and medical conditions. Read-only.
 * `advisor_id` is internal and is never shown. Fields with no value are
 * skipped, so sparse profiles simply show fewer rows.
 *
 * The parent keys this component by customer id, so every customer opens
 * collapsed.
 */
export function CustomerDetails({ customer }: { customer: Customer }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;
  const [open, setOpen] = useState(false);
  const panelId = useId();

  // Enumerated values are translated when the locale has a label and shown as stored otherwise.
  const label = (group: string, value: string | undefined) =>
    value ? String(t(`domain.${group}.${value}`, { defaultValue: value })) : null;
  const homeOwner =
    customer.home_owner === undefined || customer.home_owner === null
      ? null
      : String(t(customer.home_owner ? 'domain.yesNo.yes' : 'domain.yesNo.no'));

  const f = (key: string) => String(t(`assistant.customer.fields.${key}`, { defaultValue: key }));
  const icon = 'h-4 w-4';

  return (
    <div className="mt-5 border-t border-border pt-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={panelId}
        className="inline-flex items-center gap-1.5 rounded text-sm font-medium text-brand-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
      >
        <ChevronDown className={`h-4 w-4 transition-transform ${open ? 'rotate-180' : ''}`} />
        {open ? t('assistant.customer.details.hide') : t('assistant.customer.details.show')}
      </button>

      {open ? (
        <div id={panelId} className="mt-4 grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
          <InfoRow icon={<User className={icon} />} label={f('firstName')} value={customer.first_name} />
          <InfoRow icon={<User className={icon} />} label={f('middleName')} value={customer.middle_name} />
          <InfoRow icon={<User className={icon} />} label={f('lastName')} value={customer.last_name} />
          <InfoRow
            icon={<Cake className={icon} />}
            label={f('dateOfBirth')}
            value={fmtDate(customer.date_of_birth, locale)}
          />
          <InfoRow icon={<MapPin className={icon} />} label={f('street')} value={customer.address_line1} />
          <InfoRow icon={<MapPin className={icon} />} label={f('city')} value={customer.city} />
          <InfoRow icon={<MapPin className={icon} />} label={f('state')} value={customer.state} />
          <InfoRow icon={<MapPin className={icon} />} label={f('zip')} value={customer.zip} />
          <InfoRow
            icon={<Globe className={icon} />}
            label={f('citizenship')}
            value={label('citizenship', customer.citizenship)}
          />
          <InfoRow
            icon={<Cigarette className={icon} />}
            label={f('tobaccoStatus')}
            value={label('tobaccoStatus', customer.tobacco_status)}
          />
          <InfoRow icon={<Baby className={icon} />} label={f('dependents')} value={customer.dependents} />
          <InfoRow
            icon={<Briefcase className={icon} />}
            label={f('employmentStatus')}
            value={customer.employment_status}
          />
          <InfoRow
            icon={<Wallet className={icon} />}
            label={f('annualIncome')}
            value={customer.annual_income != null ? fmtCurrency(customer.annual_income, locale) : null}
          />
          <InfoRow
            icon={<PiggyBank className={icon} />}
            label={f('netWorthBand')}
            value={label('netWorthBand', customer.net_worth_band)}
          />
          <InfoRow icon={<Home className={icon} />} label={f('homeOwner')} value={homeOwner} />
          <InfoRow
            icon={<HeartPulse className={icon} />}
            label={f('medicalConditions')}
            value={customer.medical_conditions}
          />
        </div>
      ) : null}
    </div>
  );
}
