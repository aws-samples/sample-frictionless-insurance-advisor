import { useMemo } from 'react';

import { useTranslation } from 'react-i18next';

import { useCustomers } from '../hooks/useCustomers';
import { useSelection } from '../hooks/useSelection';
import { Alert } from '../ui';

import { Cockpit } from './Cockpit';
import { Sidebar } from './Sidebar';
import { SidebarToggle } from './SidebarToggle';

interface HomePageProps {
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
  /** Switch to the Text Assistant tab. Called after a customer (or a new prospect) is chosen. */
  onOpenText: () => void;
}

/**
 * Home tab: the customer list on the left and the Cockpit (today's priorities) filling the
 * rest of the width. There is no assistant panel here. Choosing a customer, in the list or
 * with a Cockpit button, continues in the Text Assistant tab. Voice is only reached through
 * its own tab.
 */
export function HomePage({ sidebarCollapsed, onToggleSidebar, onOpenText }: HomePageProps) {
  const { t } = useTranslation();
  const { customers, loading, error, refresh } = useCustomers();
  const { selectedId, select, startFromCockpit } = useSelection();

  const totalPolicies = useMemo(
    () => customers.reduce((sum, c) => sum + c.policies.length, 0),
    [customers]
  );

  return (
    <div className="relative flex flex-1 w-full overflow-hidden min-h-0">
      {sidebarCollapsed ? null : (
        <Sidebar
          customers={customers}
          selectedId={selectedId}
          loading={loading}
          error={error}
          onSelect={(customerId) => {
            // A customer id opens that profile; null is "+ New Prospect". Both continue in the Text tab.
            select(customerId);
            onOpenText();
          }}
          onRefresh={refresh}
        />
      )}
      <SidebarToggle collapsed={sidebarCollapsed} onToggle={onToggleSidebar} />

      <div className="h-full min-w-0 flex-1 overflow-y-auto">
        {error && !loading ? (
          <div className="p-4">
            <Alert variant="danger">
              {t('common.errors.loadFailed', { message: error })}
            </Alert>
          </div>
        ) : null}
        <Cockpit
          totalCustomers={customers.length}
          totalPolicies={totalPolicies}
          onOpen={startFromCockpit}
        />
      </div>
    </div>
  );
}
