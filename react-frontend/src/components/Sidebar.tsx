import { RefreshCw, UserPlus, Users } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { useClearRecommendations } from '../hooks/useRecommendation';
import { cn } from '../lib/cn';
import { Avatar, Badge, Button, Spinner } from '../ui';
import type { Customer } from '../types';

interface SidebarProps {
  customers: Customer[];
  selectedId: string | null;
  loading: boolean;
  error: string | null;
  onSelect: (customerId: string | null) => void;
  onRefresh: () => void;
}

export function Sidebar({
  customers,
  selectedId,
  loading,
  error,
  onSelect,
  onRefresh,
}: SidebarProps) {
  const { t } = useTranslation();
  const clearRecommendations = useClearRecommendations();

  const handleRefresh = () => {
    clearRecommendations();
    onRefresh();
  };

  return (
    <aside className="flex h-full w-[280px] shrink-0 flex-col border-r border-border bg-background-elevated/50 backdrop-blur-sm">
      {/* Heading + count */}
      <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-border">
        <div className="inline-flex items-center gap-2">
          <Users className="h-4 w-4 text-foreground-muted" />
          <span className="text-sm font-semibold">{t('assistant.sidebar.heading')}</span>
        </div>
        <span className="text-xs text-foreground-muted tabular-nums">{customers.length}</span>
      </div>

      {/* List */}
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
        {loading && customers.length === 0 ? (
          <div className="flex items-center gap-2 px-2 py-3 text-xs text-foreground-muted">
            <Spinner size="sm" />
            {t('common.states.loading')}
          </div>
        ) : null}

        {error ? (
          <div className="px-2 py-2 text-xs text-danger">{error}</div>
        ) : null}

        <ul className="flex flex-col gap-0.5">
          {customers.map((customer) => {
            const isProspect = customer.policies.every((p) => p.third_party);
            const isSelected = customer.customer_id === selectedId;
            const variant = isProspect
              ? 'info'
              : customer.status === 'Active'
                ? 'success'
                : 'warning';
            const statusKey = isProspect ? 'Prospect' : customer.status;
            const statusLabel = t(`domain.customerStatus.${statusKey}`, { defaultValue: statusKey });

            return (
              <li key={customer.customer_id}>
                <button
                  type="button"
                  onClick={() => onSelect(customer.customer_id)}
                  className={cn(
                    'group flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left transition-[background,box-shadow] duration-150',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
                    isSelected
                      ? 'bg-gradient-to-r from-brand-2/15 via-brand-1/10 to-transparent shadow-[var(--shadow-sm)] ring-1 ring-brand-2/30'
                      : 'hover:bg-background-muted'
                  )}
                  aria-current={isSelected ? 'true' : undefined}
                >
                  <Avatar name={customer.name} size="sm" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium leading-tight">
                      {customer.name}
                    </div>
                    <div className="mt-0.5 inline-flex items-center gap-1.5">
                      <Badge variant={variant} dot>
                        {statusLabel}
                      </Badge>
                    </div>
                  </div>
                </button>
              </li>
            );
          })}
        </ul>

        {!loading && customers.length === 0 && !error ? (
          <p className="px-3 py-6 text-center text-xs text-foreground-muted">
            {t('common.states.empty', { defaultValue: 'No customers yet.' })}
          </p>
        ) : null}
      </div>

      {/* Footer actions */}
      <div className="flex flex-col gap-1.5 border-t border-border px-3 py-3">
        <Button
          variant="primary"
          size="sm"
          fullWidth
          onClick={() => onSelect(null)}
        >
          <UserPlus className="h-3.5 w-3.5" />
          {t('assistant.sidebar.newProspect')}
        </Button>
        <Button variant="ghost" size="sm" fullWidth onClick={handleRefresh}>
          <RefreshCw className="h-3.5 w-3.5" />
          {t('common.actions.refresh')}
        </Button>
      </div>
    </aside>
  );
}
