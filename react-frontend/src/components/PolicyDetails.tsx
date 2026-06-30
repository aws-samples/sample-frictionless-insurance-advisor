import { useTranslation } from 'react-i18next';

import { fmtCurrency, fmtNumber } from '../lib/format';
import { Badge } from '../ui';
import type {
  DisabilityDetails,
  HealthDetails,
  LifeDetails,
  Policy,
  PropertyDetails,
  VehicleDetails,
} from '../types';

interface PolicyDetailsProps {
  policy: Policy;
}

/**
 * Renders the per-type detail payload that the API attaches to each policy.
 * Dispatches on `policy.type` and, for life insurance, on the
 * `life_details.life_type` discriminator so Term / Whole / Universal /
 * Variable each get a tailored view.
 *
 * Returns `null` when the policy carries no recognised detail payload so
 * the parent can omit the section entirely.
 */
export function PolicyDetails({ policy }: PolicyDetailsProps) {
  const { t } = useTranslation();

  let body: JSX.Element | null = null;
  let title = '';

  switch (policy.type) {
    case 'Auto Insurance':
      if (policy.vehicle) {
        title = t('assistant.policies.details.vehicle.title');
        body = <VehicleView vehicle={policy.vehicle} />;
      }
      break;
    case 'Home Insurance':
      if (policy.property) {
        title = t('assistant.policies.details.property.title');
        body = <PropertyView property={policy.property} />;
      }
      break;
    case 'Health Insurance':
      if (policy.health_details) {
        title = t('assistant.policies.details.health.title');
        body = <HealthView health={policy.health_details} />;
      }
      break;
    case 'Disability Insurance':
      if (policy.disability_details) {
        title = t('assistant.policies.details.disability.title');
        body = <DisabilityView disability={policy.disability_details} />;
      }
      break;
    case 'Life Insurance':
      if (policy.life_details) {
        title = t('assistant.policies.details.life.title');
        body = <LifeView life={policy.life_details} />;
      }
      break;
  }

  if (!body) return null;

  return (
    <div className="flex flex-col gap-1.5">
      <div className="text-xs font-semibold uppercase tracking-wide text-foreground-muted">
        {title}
      </div>
      <div className="flex flex-col gap-1">{body}</div>
    </div>
  );
}

/** Label/value row used inside each detail view. */
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-sm">
      <span className="text-foreground-muted">{label}</span>
      <span className="text-right text-foreground">{children}</span>
    </div>
  );
}

function VehicleView({ vehicle }: { vehicle: VehicleDetails }) {
  const { t } = useTranslation();
  const headline = [vehicle.year, vehicle.make, vehicle.model].filter(Boolean).join(' ');
  return (
    <>
      {headline ? <div className="text-sm">{headline}</div> : null}
      {vehicle.registration ? (
        <Field label={t('assistant.policies.details.vehicle.registration')}>
          <span className="font-mono">{vehicle.registration}</span>
        </Field>
      ) : null}
    </>
  );
}

function PropertyView({ property }: { property: PropertyDetails }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;
  return (
    <>
      {property.address ? (
        <div className="text-sm">{property.address}</div>
      ) : null}
      {property.property_type ? (
        <Field label={t('assistant.policies.details.property.type')}>{property.property_type}</Field>
      ) : null}
      {property.year_built ? (
        <Field label={t('assistant.policies.details.property.yearBuilt')}>
          <span className="tabular-nums">{property.year_built}</span>
        </Field>
      ) : null}
      {property.square_feet ? (
        <Field label={t('assistant.policies.details.property.size')}>
          {t('assistant.policies.details.property.squareFeet', {
            value: fmtNumber(property.square_feet, locale),
          })}
        </Field>
      ) : null}
    </>
  );
}

function HealthView({ health }: { health: HealthDetails }) {
  const { t } = useTranslation();
  return (
    <>
      {health.plan_tier ? (
        <Field label={t('assistant.policies.details.health.tier')}>{health.plan_tier}</Field>
      ) : null}
      {health.network ? (
        <Field label={t('assistant.policies.details.health.network')}>{health.network}</Field>
      ) : null}
      {health.dependents !== undefined ? (
        <Field label={t('assistant.policies.details.health.dependents')}>
          <span className="tabular-nums">{health.dependents}</span>
        </Field>
      ) : null}
    </>
  );
}

function DisabilityView({ disability }: { disability: DisabilityDetails }) {
  const { t } = useTranslation();
  return (
    <>
      {disability.benefit_period_years !== undefined ? (
        <Field label={t('assistant.policies.details.disability.benefitPeriod')}>
          {t('assistant.policies.details.disability.benefitYears', {
            count: disability.benefit_period_years,
          })}
        </Field>
      ) : null}
      {disability.waiting_period_days !== undefined ? (
        <Field label={t('assistant.policies.details.disability.waitingPeriod')}>
          {t('assistant.policies.details.disability.waitingDays', {
            count: disability.waiting_period_days,
          })}
        </Field>
      ) : null}
      {disability.occupation_class ? (
        <Field label={t('assistant.policies.details.disability.occupationClass')}>
          {disability.occupation_class}
        </Field>
      ) : null}
    </>
  );
}

function LifeView({ life }: { life: LifeDetails }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;

  const lifeType = life.life_type ?? 'Term Life';
  const lifeTypeLabel = t(`domain.lifeType.${lifeType}`, { defaultValue: lifeType });

  const isTerm = lifeType === 'Term Life';
  const isWhole = lifeType === 'Whole Life';
  const isUniversal = lifeType === 'Universal Life';
  const isVariable = lifeType === 'Variable Life';

  return (
    <>
      <Field label={t('assistant.policies.details.life.lifeType')}>
        <Badge variant="brand">{lifeTypeLabel}</Badge>
      </Field>

      {isTerm && life.term_years !== undefined ? (
        <Field label={t('assistant.policies.details.life.termYears')}>
          {t('assistant.policies.details.life.termYearsValue', { count: life.term_years })}
        </Field>
      ) : null}

      {isWhole && life.premium_schedule ? (
        <Field label={t('assistant.policies.details.life.premiumSchedule')}>
          {t(`domain.premiumSchedule.${life.premium_schedule}`, {
            defaultValue: life.premium_schedule,
          })}
        </Field>
      ) : null}
      {isWhole && life.dividend_option ? (
        <Field label={t('assistant.policies.details.life.dividendOption')}>
          {t(`domain.dividendOption.${life.dividend_option}`, {
            defaultValue: life.dividend_option,
          })}
        </Field>
      ) : null}

      {(isUniversal || isVariable) && life.death_benefit_option ? (
        <Field label={t('assistant.policies.details.life.deathBenefitOption')}>
          {t(`domain.deathBenefitOption.${life.death_benefit_option}`, {
            defaultValue: life.death_benefit_option,
          })}
        </Field>
      ) : null}

      {isUniversal && life.current_credited_rate ? (
        <Field label={t('assistant.policies.details.life.currentCreditedRate')}>
          {life.current_credited_rate}
        </Field>
      ) : null}
      {isUniversal && life.guaranteed_minimum_rate ? (
        <Field label={t('assistant.policies.details.life.guaranteedMinimumRate')}>
          {life.guaranteed_minimum_rate}
        </Field>
      ) : null}

      {(isWhole || isUniversal || isVariable) && life.cash_value_estimate !== undefined ? (
        <Field label={t('assistant.policies.details.life.cashValueEstimate')}>
          <span className="tabular-nums">{fmtCurrency(life.cash_value_estimate, locale)}</span>
        </Field>
      ) : null}

      {isVariable && life.sub_account_allocation ? (
        <div className="mt-1.5">
          <div className="mb-1 text-xs uppercase tracking-wide text-foreground-muted">
            {t('assistant.policies.details.life.subAccountAllocation')}
          </div>
          <SubAccountAllocation allocation={life.sub_account_allocation} />
        </div>
      ) : null}

      {life.beneficiary ? (
        <Field label={t('assistant.policies.details.life.beneficiary')}>{life.beneficiary}</Field>
      ) : null}
      {life.smoker !== undefined ? (
        <Field label={t('assistant.policies.details.life.smoker')}>
          {life.smoker
            ? t('assistant.policies.details.life.smokerYes')
            : t('assistant.policies.details.life.smokerNo')}
        </Field>
      ) : null}
    </>
  );
}

function SubAccountAllocation({ allocation }: { allocation: Record<string, number> }) {
  const { t } = useTranslation();
  const entries = Object.entries(allocation).filter(([, pct]) => pct > 0);
  const total = entries.reduce((sum, [, pct]) => sum + pct, 0) || 1;

  // Neon-tinted palette so the allocation bands feel cohesive with the brand.
  const palette = [
    'rgb(244 63 94)',     // brand-1
    'rgb(139 92 246)',    // brand-2
    'rgb(14 165 233)',    // brand-3
    'rgb(16 185 129)',    // success
    'rgb(245 158 11)',    // warning
    'rgb(99 102 241)',    // indigo
  ];

  return (
    <div className="flex flex-col gap-2">
      <div className="flex h-2 w-full overflow-hidden rounded-full">
        {entries.map(([key, pct], i) => (
          <span
            key={key}
            style={{
              width: `${(pct / total) * 100}%`,
              backgroundColor: palette[i % palette.length],
            }}
          />
        ))}
      </div>
      <div className="flex flex-col gap-1 text-sm">
        {entries.map(([key, pct], i) => {
          const label = t(`assistant.policies.details.subAccounts.${key}`, { defaultValue: key });
          return (
            <div key={key} className="flex items-center justify-between gap-2">
              <span className="inline-flex items-center gap-2 text-foreground-muted">
                <span
                  className="h-2 w-2 rounded-sm"
                  style={{ backgroundColor: palette[i % palette.length] }}
                />
                {label}
              </span>
              <span className="tabular-nums">{pct}%</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
