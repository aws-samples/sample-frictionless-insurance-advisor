import {
  CalendarClock,
  FileSearch,
  MessageSquareText,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  UserCheck,
  Wallet,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { usePriorities } from '../hooks/usePriorities';
import { cn } from '../lib/cn';
import { Alert, Avatar, Badge, Button, Card, Spinner } from '../ui';
import type { PriorityAction, PriorityReason, PriorityRule } from '../types';

export interface CockpitProps {
  totalCustomers: number;
  totalPolicies: number;
  /** Open this customer and start the matching quick prompt in the chat. */
  onOpen: (customerId: string, action: PriorityAction) => void;
}

const RULE_ICON: Record<PriorityRule, ReactNode> = {
  review_overdue: <UserCheck className="h-4 w-4" />,
  life_event_upcoming: <CalendarClock className="h-4 w-4" />,
  life_event_passed: <CalendarClock className="h-4 w-4" />,
  renewal_due: <FileSearch className="h-4 w-4" />,
  renewal_passed: <ShieldAlert className="h-4 w-4" />,
  over_cap: <Wallet className="h-4 w-4" />,
  prospect_quiet: <MessageSquareText className="h-4 w-4" />,
};

// Rules that are already late (or about to be) are shown in a warmer colour.
const RULE_TONE: Record<PriorityRule, 'danger' | 'warning' | 'info'> = {
  review_overdue: 'warning',
  life_event_upcoming: 'info',
  life_event_passed: 'warning',
  renewal_due: 'info',
  renewal_passed: 'danger',
  over_cap: 'warning',
  prospect_quiet: 'info',
};

// The icon in front of each reason takes the tone of its rule.
const RULE_TONE_TEXT: Record<'danger' | 'warning' | 'info', string> = {
  danger: 'text-danger',
  warning: 'text-warning',
  info: 'text-foreground-muted',
};

/**
 * The welcome area when no customer is selected: today's priorities.
 * Each row names the customer, why they are on the list, and offers one
 * click to open them with the right starting prompt (annual review,
 * meeting preparation or a drafted message).
 */
export function Cockpit({ totalCustomers, totalPolicies, onOpen }: CockpitProps) {
  const { t } = useTranslation();
  const { data, loading, error, reload } = usePriorities();

  // Renewals of our own policies read without the insurer in brackets.
  const reasonText = (r: PriorityReason): string => {
    const own = (r.rule === 'renewal_due' || r.rule === 'renewal_passed') && r.params.own === 1;
    return t(`assistant.cockpit.rules.${own ? `${r.rule}_own` : r.rule}`, { ...r.params, defaultValue: r.reason });
  };

  return (
    <div className="p-6 sm:p-8">
      <div className="flex items-start gap-4">
        <Sparkles className="mt-1 h-6 w-6 text-brand-2" />
        <div className="min-w-0 flex-1">
          <h2 className="text-2xl font-bold tracking-tight">
            <span className="text-brand-gradient">{t('assistant.welcome.heading')}</span>
          </h2>
          <p className="mt-1 max-w-prose text-sm text-foreground-muted">
            {t('assistant.welcome.body')}
          </p>
        </div>
      </div>

      <div className="mt-6 grid grid-cols-2 gap-3">
        <Card feature className="p-4">
          <div className="text-xs font-medium uppercase tracking-wide text-foreground-muted">
            {t('assistant.welcome.totalCustomers')}
          </div>
          <div className="mt-1 text-2xl font-bold tabular-nums">
            <span className="text-brand-gradient">{totalCustomers}</span>
          </div>
        </Card>
        <Card feature className="p-4">
          <div className="text-xs font-medium uppercase tracking-wide text-foreground-muted">
            {t('assistant.welcome.activePolicies')}
          </div>
          <div className="mt-1 text-2xl font-bold tabular-nums">
            <span className="text-brand-gradient">{totalPolicies}</span>
          </div>
        </Card>
      </div>

      <section className="mt-8" aria-labelledby="cockpit-heading">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <h3 id="cockpit-heading" className="text-lg font-semibold tracking-tight">
              {t('assistant.cockpit.heading')}
            </h3>
            {data ? (
              <p className="text-xs text-foreground-muted">
                {t('assistant.cockpit.summary', {
                  needing: data.customers_needing_attention,
                  total: data.total_customers,
                })}
              </p>
            ) : null}
          </div>
          <Button variant="ghost" size="sm" onClick={reload} disabled={loading}>
            <RefreshCw className={loading ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'} />
            {t('assistant.cockpit.refresh')}
          </Button>
        </div>

        {error ? (
          <div className="mt-3">
            <Alert variant="danger">{t('assistant.cockpit.error', { message: error })}</Alert>
          </div>
        ) : null}

        {loading && !data ? (
          <div className="mt-6 flex items-center gap-2 text-sm text-foreground-muted">
            <Spinner size="sm" />
            {t('assistant.cockpit.loading')}
          </div>
        ) : null}

        {data && data.items.length === 0 && !error ? (
          <p className="mt-4 text-sm text-foreground-muted">{t('assistant.cockpit.empty')}</p>
        ) : null}

        <ul className="mt-4 space-y-3">
          {(data?.items ?? []).map((item) => (
            <li key={item.customer_id}>
              <Card className="p-4">
                <div className="flex items-start gap-3">
                  <Avatar name={item.customer_name} size="md" />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate font-semibold">{item.customer_name}</span>
                      {item.is_prospect ? (
                        <Badge variant="info">{t('assistant.cockpit.prospect')}</Badge>
                      ) : null}
                    </div>
                    <ul className="mt-1 space-y-1">
                      {item.reasons.map((r, i) => (
                        <li key={`${r.rule}-${i}`} className="flex items-start gap-2 text-sm">
                          <span className={cn('mt-0.5 shrink-0', RULE_TONE_TEXT[RULE_TONE[r.rule]])}>
                            {RULE_ICON[r.rule]}
                          </span>
                          <span className="min-w-0 break-words">{reasonText(r)}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <Button
                    variant="primary"
                    size="sm"
                    className="shrink-0"
                    onClick={() => onOpen(item.customer_id, item.suggested_action)}
                  >
                    {t(`assistant.cockpit.actions.${item.suggested_action}`)}
                  </Button>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
