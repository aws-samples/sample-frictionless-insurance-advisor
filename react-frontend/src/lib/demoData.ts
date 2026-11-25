import type { FormFieldDef, FormSchema } from '../types';
import { isRequired, isVisible } from './formRules';

/**
 * Sample answers for the "Fill demo data" button on every application form.
 *
 * Demo only: the same values for every customer, so a form can be completed (and Send for
 * Signature tried) without typing each field. Only fields that are empty are filled, so
 * whatever the profile or the assistants already supplied is kept. The sample person is
 * fictional; nothing here is real.
 *
 * A value is used when it is listed here; any other field that is required but not listed gets
 * a neutral value for its type, so a new form still completes. Optional fields that are not
 * listed (a second beneficiary, the owner and payor, riders) are left blank on purpose:
 * a made-up value there would change what the application means.
 */
const BY_PATH: Record<string, string> = {
  // Applicant (nine of the forms)
  'applicant.full_name': 'Alex Sample',
  'applicant.date_of_birth': '1985-06-15',
  'applicant.gender': 'male',
  'applicant.occupation': 'Consultant',
  'applicant.annual_income': '100000',
  'applicant.email': 'alex.sample@example.com',
  'applicant.phone': '(555) 010-0100',
  'applicant.address': '100 Demo Street, Austin, TX 78701',
  // Health questions shared by several forms
  'health.tobacco_status': 'never',
  'health.height_cm': '178',
  'health.weight_kg': '80',
  'health.existing_conditions': 'None',
  'history.family_serious_illness': 'false',
  // Life
  'coverage.sum_assured': '500000',
  'coverage.term_years': '20',
  'coverage.premium_frequency': 'monthly',
  'coverage.premium_structure': 'level',
  'coverage.premium_level': 'target',
  'coverage.premium_schedule': 'lifetime',
  'coverage.dividend_option': 'cash',
  'coverage.death_benefit_option': 'option_a',
  'coverage.sub_account_allocation': 'Balanced 60%, Bond 40%',
  'coverage.risk_acknowledged': 'true',
  'beneficiary.name': 'Jamie Sample',
  'beneficiary.relationship': 'Spouse',
  'beneficiary.percentage': '100',
  // Critical illness, disability
  'coverage.lump_sum': '100000',
  'coverage.illness_tier': 'standard',
  'coverage.cover_term': '20',
  'coverage.monthly_benefit': '5000',
  'coverage.benefit_period': 'to_retirement',
  'coverage.waiting_period': '90',
  'coverage.occupation_class': 'class_2',
  'coverage.definition': 'own_occupation',
  'coverage.coverage_type': 'long_term',
  // Health
  'coverage.tier': 'standard',
  'coverage.annual_limit': '50000',
  'coverage.deductible': '500',
  'coverage.network': 'standard',
  'coverage.reimbursement': 'direct_billing',
  // Home
  'property.address': '100 Demo Street, Austin, TX 78701',
  'property.type': 'single_family',
  'property.year_built': '2005',
  'property.claims_last_5_years': '0',
  'coverage.dwelling_value': '450000',
  'coverage.contents_percent': '70',
  'coverage.liability': '300000',
  'coverage.living_expenses_months': '12',
  // Motor
  'driver.years_licensed': '10',
  'driver.accidents_5y': '0',
  'driver.violations_5y': '0',
  'vehicle.make': 'Toyota',
  'vehicle.model': 'Camry',
  'vehicle.year': '2021',
  'vehicle.value': '25000',
  'coverage.type': 'comprehensive',
  'coverage.liability_limits': '50k_100k',
  'coverage.usage': 'personal',
  'coverage.annual_mileage': '5000_10000',
  'coverage.driver_cover': 'named_drivers',
  // Travel
  'trip.destination': 'Lisbon, Portugal',
  'trip.start_date': '2027-03-10',
  'trip.end_date': '2027-03-24',
  'trip.travellers': '2',
  'trip.prepaid_cost': '4000',
  'coverage.geographic': 'continental',
  'coverage.duration': 'single_trip',
  'coverage.level': 'standard',
  'coverage.medical': '500000',
  // Universal Life (the long application)
  'coverage.face_amount': '500000',
  'coverage.nlg_to_age': '95',
  'insured.first_name': 'Alex',
  'insured.last_name': 'Sample',
  'insured.date_of_birth': '1985-06-15',
  'insured.gender': 'male',
  'insured.address_line1': '100 Demo Street',
  'insured.city': 'Austin',
  'insured.state': 'TX',
  'insured.zip': '78701',
  'insured.ssn': '000-12-3456', // not a valid number; typed on screen like the real one, never sent to an assistant
  'insured.marital_status': 'Married',
  'insured.citizenship': 'us_citizen',
  'insured.occupation': 'Consultant',
  'insured.annual_income': '100000',
  'insured.tobacco_status': 'never',
  'insured.email': 'alex.sample@example.com',
  'insured.phone': '(555) 010-0100',
  'beneficiary1.name': 'Jamie Sample',
  'beneficiary1.relationship': 'Spouse',
  'beneficiary1.type': 'primary',
  'beneficiary1.pct': '100',
  'beneficiary1.revocable': 'true',
  'premium.planned_amount': '400',
  'premium.mode': 'monthly',
  'premium.payment_method': 'bank_draft',
  'premium.exchange_1035': 'false',
  'existing.has_coverage': 'false',
  'existing.total_amount': '250000',
  'existing.replacing': 'false',
  'existing.purpose': 'income_replacement',
  'financial.net_worth': '100k_250k',
};

/** The value to use for a required field that is not listed above. */
function neutralValue(field: FormFieldDef): string {
  switch (field.type) {
    case 'select':
      return field.options?.[0]?.value ?? '';
    case 'boolean':
      return 'false';
    case 'number':
      return '1';
    case 'currency':
      return '1000';
    case 'date':
      return '2027-01-01';
    default:
      return 'Sample';
  }
}

/** A yes/no medical question answers No; a details box that has been called for gets a sample line. */
function listedValue(field: FormFieldDef): string | undefined {
  const listed = BY_PATH[field.path];
  if (listed !== undefined) return listed;
  if (/^medical\.q\d+$/.test(field.path)) return 'false';
  if (/^medical\.q\d+_details$/.test(field.path)) return 'Sample detail for demonstration.';
  return undefined;
}

/**
 * What to put in each empty field of `schema`, given the answers so far. Fields that only
 * appear (or become required) after another answer are picked up by repeating until nothing
 * new is added. Returns path -> value for the empty fields only.
 */
export function demoFill(schema: FormSchema, current: Record<string, string>): Record<string, string> {
  const values: Record<string, string> = { ...current };
  const added: Record<string, string> = {};
  for (let pass = 0; pass < 6; pass += 1) {
    let changed = false;
    for (const section of schema.sections) {
      for (const field of section.fields) {
        if (values[field.path] !== undefined && values[field.path] !== '') continue;
        if (!isVisible(field, values)) continue;
        const value = listedValue(field) ?? (isRequired(field, values) ? neutralValue(field) : undefined);
        if (value === undefined || value === '') continue;
        // A choice must be one of the form's own options.
        if (field.type === 'select' && !(field.options ?? []).some((o) => o.value === value)) {
          const first = field.options?.[0]?.value;
          if (!first) continue;
          values[field.path] = first;
          added[field.path] = first;
        } else {
          values[field.path] = value;
          added[field.path] = value;
        }
        changed = true;
      }
    }
    if (!changed) break;
  }
  return added;
}
