import { useMemo, useState } from 'react';

import { useTranslation } from 'react-i18next';

import { useCustomers } from '../hooks/useCustomers';
import { Alert } from '../ui';

import { CustomerPanel } from './CustomerPanel';
import { Sidebar } from './Sidebar';
import { SidebarToggle } from './SidebarToggle';
import { SplitWorkspace } from './SplitWorkspace';
import { VoicePanel } from './VoicePanel';

interface VoicePageProps {
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
}

/**
 * Voice page: sidebar + resizable split of customer profile + voice chat panel.
 *
 * Mirrors the AssistantPage layout (and the AssistantPage's "+ New Prospect"
 * flow) so the experience and styling stay consistent. The right-hand panel
 * is a voice-first interface backed by the Nova Sonic voice runtime, which
 * has the same gateway tools as the insurance agent including
 * create_profile/update_profile for prospect onboarding.
 */
export function VoicePage({ sidebarCollapsed, onToggleSidebar }: VoicePageProps) {
  const { t } = useTranslation();
  const { customers, loading, error, refresh } = useCustomers();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [prospectMode, setProspectMode] = useState<boolean>(false);

  const handleSelect = (customerId: string | null) => {
    if (customerId === null) {
      setSelectedId(null);
      setProspectMode(true);
    } else {
      setSelectedId(customerId);
      setProspectMode(false);
    }
  };

  const selectedCustomer = useMemo(
    () => customers.find((c) => c.customer_id === selectedId) ?? null,
    [customers, selectedId]
  );

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
          onSelect={handleSelect}
          onRefresh={refresh}
        />
      )}
      <SidebarToggle collapsed={sidebarCollapsed} onToggle={onToggleSidebar} />

      <div className="flex-1 min-w-0">
        {error && !loading ? (
          <div className="p-4">
            <Alert variant="danger">
              {t('common.errors.loadFailed', { message: error })}
            </Alert>
          </div>
        ) : null}

        <SplitWorkspace
          storageId="insadv.workspace.split"
          left={
            <CustomerPanel
              customer={selectedCustomer}
              totalCustomers={customers.length}
              totalPolicies={totalPolicies}
            />
          }
          right={
            selectedCustomer ? (
              <VoicePanel customer={selectedCustomer} />
            ) : prospectMode ? (
              <VoicePanel customer={null} />
            ) : (
              <div className="p-6">
                <Alert variant="info">{t('assistant.chat.selectCustomer')}</Alert>
              </div>
            )
          }
        />
      </div>
    </div>
  );
}
