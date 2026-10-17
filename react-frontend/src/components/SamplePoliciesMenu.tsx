import { useEffect, useRef, useState } from 'react';

import { Download, FileText } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { cn } from '../lib/cn';
import type { Customer } from '../types';

interface SamplePolicy {
  /** matching customer_id from the mock profiles seed (lambda/mock_data/profiles.json) */
  customerId: string;
  /** filename stem under react-frontend/public/mock-policies/ — both .md and .pdf are present */
  filename: string;
  /** human-readable label shown in the popover */
  label: string;
  /** short description shown beneath the label */
  description: string;
}

/**
 * Per-customer mock policy documents. The agent's extract_policy tool will
 * read the carrier name, type, dates, premium, and policy fields from any
 * of these and offer to add the policy as a third-party record on that
 * customer's profile.
 *
 * Each entry is hand-picked so the carrier and line of business do NOT
 * duplicate any third-party policy already in DynamoDB for that customer
 * (see lambda/mock_data/policies.json), which keeps the demo realistic.
 *
 * Source of truth for the policy contents: s3-data/mock-policies/. Edit
 * there, not here. scripts/sync-mock-policies.sh copies them into
 * public/mock-policies/ on predev/prebuild.
 */
const SAMPLE_POLICIES: SamplePolicy[] = [
  {
    customerId: '1',
    filename: 'johnson-sarah-brookmere-disability',
    label: 'Brookmere Income Shield LTD',
    description: '$5K/mo benefit, 5-year period, 90-day elimination — covers a real disability gap',
  },
  {
    customerId: '2',
    filename: 'rodriguez-emily-bigrival-term-life',
    label: 'BigRival Term Life 15',
    description: '$250K, 15-year level term, $18.50/mo — adds first life policy on file',
  },
  {
    customerId: '3',
    filename: 'williams-robert-quicksafe-health',
    label: 'QuickSafe FamilyCare Premium',
    description: 'Premium PPO, family of 4, $1.5M aggregate — fills the missing health line',
  },
  {
    customerId: '4',
    filename: 'thompson-lisa-bigrival-auto',
    label: 'BigRival SafeDrive Auto',
    description: '2022 Subaru Forester, $300K/$500K liability — adds first auto policy',
  },
  {
    customerId: '5',
    filename: 'chen-michael-bigrival-home',
    label: 'BigRival HomeSecure Premier',
    description: 'IL home, $620K dwelling, annual premium — fills the missing home line',
  },
  {
    customerId: '6',
    filename: 'davis-amanda-brookmere-term-life',
    label: 'Brookmere FoundersTerm Life',
    description: '$300K, 10-year term with founder buyout rider — first life policy',
  },
  {
    customerId: '7',
    filename: 'park-daniel-quicksafe-auto',
    label: 'QuickSafe RoadStart Auto',
    description: '2018 Honda Civic, telematics-tracked, monthly EFT — first policy on file',
  },
  {
    customerId: '8',
    filename: 'martinez-jessica-bigrival-home',
    label: 'BigRival HomeSecure Coastal',
    description: 'CA coastal home, $740K dwelling, with earthquake rider — fills the missing home line',
  },
  {
    customerId: '9',
    filename: 'okafor-david-bigrival-disability',
    label: 'BigRival CareerShield Income Protection',
    description: '$8K/mo benefit to age 65 — fills the missing disability line',
  },
  {
    customerId: '10',
    filename: 'patel-priya-bigrival-health',
    label: 'BigRival PrimeCare Family Health',
    description: 'Family PPO, $250K annual limit per person — fills the missing health line',
  },
  {
    customerId: '11',
    filename: 'bennett-marcus-brookmere-critical-illness',
    label: 'Brookmere Critical Illness Plus',
    description: '$150K lump sum — a competitor critical illness cover',
  },
  {
    customerId: '12',
    filename: 'lee-hannah-quicksafe-renters',
    label: 'QuickSafe Nest Renters',
    description: 'Seattle apartment, $300K liability — fills the missing renters line',
  },
  {
    customerId: '13',
    filename: 'mendez-carlos-brookmere-critical-illness',
    label: 'Brookmere Critical Illness Plus',
    description: '$100K lump sum, smoker rate class — a competitor critical illness cover',
  },
  {
    customerId: '14',
    filename: 'grant-olivia-brookmere-travel',
    label: 'Brookmere BasicCover Travel',
    description: 'Annual multi-trip family policy, 45 days per trip — adds first travel policy',
  },
  {
    customerId: '15',
    filename: 'brooks-ethan-quicksafe-home',
    label: 'QuickSafe Nest Home',
    description: 'Raleigh home, $680K dwelling — fills the missing home line',
  },
  {
    customerId: '16',
    filename: 'laurent-sophie-quicksafe-auto',
    label: 'QuickSafe Drive Auto',
    description: 'Chicago, 100/300 liability, monthly payments — first policy on file',
  },
  {
    customerId: '17',
    filename: 'nguyen-tom-brookmere-auto',
    label: 'Brookmere BasicCover Auto',
    description: 'Austin, self-employed use rider — adds an auto policy next to his BigRival term life',
  },
  {
    customerId: '18',
    filename: 'haddad-nadia-brookmere-term-life',
    label: 'Brookmere BasicCover Life 20',
    description: '$400K, 20-year level term — first life policy',
  },
  {
    customerId: '19',
    filename: 'schmidt-leo-bigrival-auto',
    label: 'BigRival SafeHaven Motor',
    description: 'Work van with business use, 250/500 liability — adds an auto policy next to his QuickSafe home',
  },
];

interface SamplePoliciesMenuProps {
  /**
   * The currently-selected customer in the panel. Determines which sample
   * policy is offered. Pass `null` (e.g. + New Prospect mode) and the
   * menu hides itself — there's no good default policy to suggest when
   * no customer is in scope.
   */
  customer: Customer | null;
}

/**
 * Tiny "download sample" button + popover. Lets demo visitors grab a
 * synthetic 3rd-party policy document scoped to the currently-selected
 * customer, so the upload flow shows a realistic name + address mapping
 * back to the profile in DynamoDB. Hidden in prospect mode.
 */
export function SamplePoliciesMenu({ customer }: SamplePoliciesMenuProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (!containerRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  if (!customer) {
    // No customer selected (prospect mode) — nothing to download. The
    // upload flow still works for prospect mode, but the demo offers no
    // canned per-customer file in that case.
    return null;
  }

  const policy = SAMPLE_POLICIES.find((p) => p.customerId === customer.customer_id);
  if (!policy) {
    return null;
  }

  return (
    <div ref={containerRef} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          'inline-flex items-center gap-1 text-[11px] text-foreground-muted',
          'underline decoration-dotted underline-offset-2',
          'hover:text-brand-2 transition-colors'
        )}
      >
        <Download className="h-3 w-3" />
        {t('assistant.chat.samplePolicies.cta')}
      </button>

      {open ? (
        <div
          role="menu"
          className={cn(
            'absolute bottom-full left-0 z-50 mb-2 w-80 rounded-md border border-border',
            'bg-background-elevated shadow-[var(--shadow-md)]',
            'p-2'
          )}
        >
          <div className="px-2 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-foreground-muted">
            {t('assistant.chat.samplePolicies.heading', { name: customer.name })}
          </div>
          <div className="text-[11px] text-foreground-muted px-2 pb-2">
            {t('assistant.chat.samplePolicies.subtext')}
          </div>
          <ul className="flex flex-col gap-1">
            <li className="rounded px-2 py-1.5 hover:bg-background-muted/60">
              <div className="flex items-start gap-2">
                <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand-2" />
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-medium text-foreground">{policy.label}</div>
                  <div className="text-[11px] text-foreground-muted">{policy.description}</div>
                  <div className="mt-1 flex gap-2">
                    <a
                      href={`${import.meta.env.BASE_URL}mock-policies/${policy.filename}.pdf`}
                      download
                      onClick={() => setOpen(false)}
                      className="text-[11px] font-medium text-brand-2 underline decoration-brand-2/40 underline-offset-2 hover:decoration-brand-2"
                    >
                      PDF
                    </a>
                    <a
                      href={`${import.meta.env.BASE_URL}mock-policies/${policy.filename}.md`}
                      download
                      onClick={() => setOpen(false)}
                      className="text-[11px] font-medium text-brand-2 underline decoration-brand-2/40 underline-offset-2 hover:decoration-brand-2"
                    >
                      Markdown
                    </a>
                  </div>
                </div>
              </div>
            </li>
          </ul>
          <div className="border-t border-border mt-2 px-2 pt-1.5 text-[10px] text-foreground-muted">
            {t('assistant.chat.samplePolicies.notice')}
          </div>
        </div>
      ) : null}
    </div>
  );
}
