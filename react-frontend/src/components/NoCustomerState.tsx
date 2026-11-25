import { UserPlus, Users } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Button } from '../ui';

interface NoCustomerStateProps {
  /** Which tab is empty: picks the matching "select a customer" sentence. */
  kind: 'chat' | 'voice';
  /** Starts the new-prospect flow, the same action as the sidebar's "+ New Prospect" button. */
  onNewProspect: () => void;
}

/**
 * Main area of the Text and Voice tabs while no customer is selected (for example when the
 * advisor opens the tab straight from Home). The customer list stays in the sidebar; this
 * asks the advisor to pick one and offers the new-prospect flow, which also works when the
 * sidebar is collapsed.
 */
export function NoCustomerState({ kind, onNewProspect }: NoCustomerStateProps) {
  const { t } = useTranslation();
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
      <Users className="h-8 w-8 text-foreground-muted" aria-hidden />
      <p className="max-w-prose text-sm text-foreground-muted">
        {t(kind === 'chat' ? 'assistant.chat.selectCustomer' : 'assistant.voice.selectCustomer')}
      </p>
      <Button variant="primary" size="sm" onClick={onNewProspect}>
        <UserPlus className="h-3.5 w-3.5" />
        {t('assistant.sidebar.newProspect')}
      </Button>
    </div>
  );
}
