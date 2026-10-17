import { useState } from 'react';
import { Check, GraduationCap, X, TriangleAlert } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '../lib/cn';
import { Alert, Badge, Button, IconButton, Spinner } from '../ui';
import type {
  Customer,
  RoleplayConfig,
  RoleplayCriterionId,
  RoleplayDifficulty,
  RoleplayPersona,
  RoleplayScoreResponse,
} from '../types';

/** What the AI customer is told about itself, taken from the record already on screen. */
export function personaFromCustomer(customer: Customer): RoleplayPersona {
  let age: number | undefined;
  if (customer.date_of_birth) {
    const dob = new Date(customer.date_of_birth);
    if (!Number.isNaN(dob.getTime())) {
      age = Math.floor((Date.now() - dob.getTime()) / (365.25 * 86_400_000));
    }
  }
  return {
    name: customer.name,
    age,
    marital_status: customer.marital_status,
    dependents: customer.dependents,
    occupation: customer.occupation,
    employment_status: customer.employment_status,
    annual_income: customer.annual_income,
    home_owner: customer.home_owner,
    // Health details stay out on purpose; the notes are the advisor's own.
    notes: (customer.notes ?? []).slice(-8).map((n) => ({ type: n.type, text: n.text })),
    policies: customer.policies.map((p) => ({
      product_name: p.product_name,
      type: p.type,
      insurer: p.insurer,
      third_party: p.third_party,
    })),
  };
}

// The focus text goes to the model as written; the labels are translated.
const FOCUS_OPTIONS: { value: string; labelKey: string }[] = [
  { value: '', labelKey: 'assistant.roleplay.focus.any' },
  { value: 'The cost is too high', labelKey: 'assistant.roleplay.focus.cost' },
  { value: 'I already have cover through work', labelKey: 'assistant.roleplay.focus.work' },
  { value: "I'm too young to need this", labelKey: 'assistant.roleplay.focus.young' },
  { value: 'I want to compare with my current insurer', labelKey: 'assistant.roleplay.focus.compare' },
  { value: 'I need to think about it', labelKey: 'assistant.roleplay.focus.think' },
];
const DIFFICULTIES: RoleplayDifficulty[] = ['friendly', 'neutral', 'sceptical'];

const selectClass =
  'h-10 w-full rounded-lg border border-border bg-background px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-brand-2/40';

interface SetupProps {
  customer: Customer;
  onStart: (config: RoleplayConfig) => void;
  onCancel: () => void;
}

export function RoleplaySetup({ customer, onStart, onCancel }: SetupProps) {
  const { t } = useTranslation();
  const [difficulty, setDifficulty] = useState<RoleplayDifficulty>('neutral');
  const [focus, setFocus] = useState<string>('');
  return (
    <div className="border-b border-border bg-background-muted/40 px-6 py-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="inline-flex items-center gap-2">
          <GraduationCap className="h-4 w-4 text-brand-2" />
          <h4 className="text-sm font-semibold">{t('assistant.roleplay.setupTitle', { name: customer.name })}</h4>
        </div>
        <IconButton aria-label={t('common.actions.close')} variant="ghost" size="sm" onClick={onCancel}>
          <X className="h-4 w-4" />
        </IconButton>
      </div>
      <p className="mb-3 text-xs text-foreground-muted">{t('assistant.roleplay.setupIntro')}</p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="rp-difficulty" className="mb-1 block text-xs uppercase tracking-wide text-foreground-muted">
            {t('assistant.roleplay.difficulty')}
          </label>
          <select
            id="rp-difficulty"
            value={difficulty}
            onChange={(e) => setDifficulty(e.target.value as RoleplayDifficulty)}
            className={selectClass}
          >
            {DIFFICULTIES.map((d) => (
              <option key={d} value={d}>
                {t(`assistant.roleplay.level.${d}`)}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="rp-focus" className="mb-1 block text-xs uppercase tracking-wide text-foreground-muted">
            {t('assistant.roleplay.focusLabel')}
          </label>
          <select id="rp-focus" value={focus} onChange={(e) => setFocus(e.target.value)} className={selectClass}>
            {FOCUS_OPTIONS.map((f) => (
              <option key={f.value} value={f.value}>
                {t(f.labelKey as never)}
              </option>
            ))}
          </select>
        </div>
      </div>
      <p className="mt-3 text-xs text-foreground-muted">{t('assistant.roleplay.privacy')}</p>
      <div className="mt-3 flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel}>
          {t('common.actions.cancel')}
        </Button>
        <Button
          variant="primary"
          size="sm"
          onClick={() => onStart({ difficulty, focus, persona: personaFromCustomer(customer) })}
        >
          {t('assistant.roleplay.start')}
        </Button>
      </div>
    </div>
  );
}

interface BannerProps {
  customerName: string;
  difficulty: RoleplayDifficulty;
  canEnd: boolean;
  onEnd: () => void;
  onLeave: () => void;
}

/** Shown for the whole practice session so it can never be mistaken for a real conversation. */
export function RoleplayBanner({ customerName, difficulty, canEnd, onEnd, onLeave }: BannerProps) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-brand-2/30 bg-brand-2/10 px-6 py-2">
      <div className="min-w-0 text-sm">
        <span className="inline-flex items-center gap-1.5 font-semibold">
          <GraduationCap className="h-4 w-4 text-brand-2" />
          {t('assistant.roleplay.banner', { name: customerName })}
        </span>
        <span className="ml-2 text-xs text-foreground-muted">{t(`assistant.roleplay.level.${difficulty}`)}</span>
        <p className="text-xs text-foreground-muted">{t('assistant.roleplay.bannerHint')}</p>
      </div>
      <div className="flex shrink-0 gap-2">
        <Button variant="ghost" size="sm" onClick={onLeave}>
          {t('assistant.roleplay.leave')}
        </Button>
        <Button variant="primary" size="sm" disabled={!canEnd} onClick={onEnd}>
          {t('assistant.roleplay.endAndScore')}
        </Button>
      </div>
    </div>
  );
}

interface ScoreProps {
  loading: boolean;
  error: string;
  result: RoleplayScoreResponse | null;
  onAgain: () => void;
  onClose: () => void;
}

const VERDICT_VARIANT = { supported: 'success', contradicted: 'danger', not_in_sheets: 'warning' } as const;

export function RoleplayScoreView({ loading, error, result, onAgain, onClose }: ScoreProps) {
  const { t } = useTranslation();
  return (
    <div className="max-h-[60vh] overflow-y-auto border-b border-border bg-background-muted/40 px-6 py-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="inline-flex items-center gap-2">
          <GraduationCap className="h-4 w-4 text-brand-2" />
          <h4 className="text-sm font-semibold">{t('assistant.roleplay.feedbackTitle')}</h4>
        </div>
        <IconButton aria-label={t('common.actions.close')} variant="ghost" size="sm" onClick={onClose}>
          <X className="h-4 w-4" />
        </IconButton>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 py-2 text-sm text-foreground-muted">
          <Spinner size="sm" />
          {t('assistant.roleplay.scoring')}
        </div>
      ) : null}
      {error ? <Alert variant="danger">{t('assistant.roleplay.scoreError', { message: error })}</Alert> : null}

      {result?.insufficient ? (
        <p className="py-2 text-sm text-foreground-muted">{t('assistant.roleplay.insufficient')}</p>
      ) : null}

      {result && !result.insufficient && result.criteria ? (
        <div className="space-y-4">
          <div className="flex items-baseline gap-2">
            <span className="text-3xl font-bold tabular-nums text-brand-gradient">{result.overall?.toFixed(1)}</span>
            <span className="text-sm text-foreground-muted">/ 5</span>
          </div>
          {result.summary ? <p className="text-sm">{result.summary}</p> : null}

          <ul className="space-y-3">
            {result.criteria.map((c) => (
              <li key={c.id}>
                <div className="flex items-center justify-between gap-2 text-sm">
                  <span className="font-medium">{t(`assistant.roleplay.criteria.${c.id as RoleplayCriterionId}`)}</span>
                  <span className="tabular-nums text-foreground-muted">{c.score}/5</span>
                </div>
                <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-background-muted" aria-hidden="true">
                  <div
                    className={cn('h-full rounded-full', c.score >= 4 ? 'bg-success' : c.score >= 2 ? 'bg-brand-2' : 'bg-warning')}
                    style={{ width: `${(c.score / 5) * 100}%` }}
                  />
                </div>
                {c.quote ? <p className="mt-1 text-xs italic text-foreground-muted">&ldquo;{c.quote}&rdquo;</p> : null}
                {c.suggestion ? <p className="mt-0.5 text-xs">{c.suggestion}</p> : null}
              </li>
            ))}
          </ul>

          {result.claims_check && result.claims_check.length > 0 ? (
            <div>
              <h5 className="mb-1 text-xs font-semibold uppercase tracking-wide text-foreground-muted">
                {t('assistant.roleplay.claimsTitle')}
              </h5>
              <ul className="space-y-1.5">
                {result.claims_check.map((c, i) => (
                  <li key={i} className="flex items-start gap-2 text-xs">
                    <Badge variant={VERDICT_VARIANT[c.verdict]} className="shrink-0">
                      {t(`assistant.roleplay.verdict.${c.verdict}`)}
                    </Badge>
                    <span className="min-w-0">
                      {c.claim}
                      {c.note ? <span className="text-foreground-muted"> ({c.note})</span> : null}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {result.next_time && result.next_time.length > 0 ? (
            <div>
              <h5 className="mb-1 text-xs font-semibold uppercase tracking-wide text-foreground-muted">
                {t('assistant.roleplay.nextTime')}
              </h5>
              <ul className="space-y-1 text-sm">
                {result.next_time.map((x, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand-2" />
                    <span>{x}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <p className="flex items-start gap-1.5 text-[11px] text-foreground-muted">
            <TriangleAlert className="mt-0.5 h-3 w-3 shrink-0" />
            {t('assistant.roleplay.coachingNote')}
          </p>
        </div>
      ) : null}

      {!loading ? (
        <div className="mt-3 flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>
            {t('assistant.roleplay.finish')}
          </Button>
          <Button variant="primary" size="sm" onClick={onAgain}>
            {t('assistant.roleplay.again')}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
