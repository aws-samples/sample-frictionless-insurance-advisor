import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useApplicationForm } from '../hooks/useApplicationForm';
import { useCustomers } from '../hooks/useCustomers';
import { useSelection } from '../hooks/useSelection';
import type { VoiceFormHandlers } from '../hooks/useVoiceChat';
import { Alert } from '../ui';
import { CustomerPanel } from './CustomerPanel';
import { FormPanel } from './FormPanel';
import { NoCustomerState } from './NoCustomerState';
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
 *
 * When the agent starts an application, the left panel swaps from the customer
 * profile to the application form. Voice and transcript stay visible on the
 * right so the advisor can watch fields land as they talk. Both the open and
 * the per-field fills are driven by tool calls from the voice runtime — the UI
 * never guesses when an application has begun.
 */
export function VoicePage({ sidebarCollapsed, onToggleSidebar }: VoicePageProps) {
  const { t } = useTranslation();
  const { customers, loading, error, refresh } = useCustomers();
  // Shared with the Text page, so switching tab keeps the same customer.
  const { selectedId, prospectMode, select: handleSelect } = useSelection();
  const selectedCustomer = useMemo(
    () => customers.find((c) => c.customer_id === selectedId) ?? null,
    [customers, selectedId]
  );

  const form = useApplicationForm(selectedCustomer, prospectMode);
  const { activeForm, prefill } = form;

  const formHandlers = useMemo<VoiceFormHandlers>(
    () => ({ onFormOpen: form.openForm, onFormFill: form.fillField }),
    [form.openForm, form.fillField]
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

        {selectedId === null && !prospectMode && !activeForm ? (
          <NoCustomerState kind="voice" onNewProspect={() => handleSelect(null)} />
        ) : (
        <SplitWorkspace
          storageId="insadv.workspace.split"
          left={
            activeForm ? (
              <FormPanel
                schema={activeForm}
                fields={form.fields}
                progress={form.progress}
                carriedOver={form.carriedOver}
                onFieldChange={form.setFieldManually}
                onConfirmField={form.confirmField}
                onClose={form.closeForm}
                customerName={selectedCustomer?.name}
                customerId={selectedCustomer?.customer_id ?? null}
                savedRef={form.savedRef}
                onSaved={form.rememberSavedRef}
              />
            ) : (
              <CustomerPanel
                customer={selectedCustomer}
                onOpenForm={form.openForm}
              />
            )
          }
          right={
            selectedCustomer ? (
              <VoicePanel
                customer={selectedCustomer}
                formHandlers={formHandlers}
                formPrefill={prefill}
                onNoteSaved={refresh}
              />
            ) : prospectMode ? (
              <VoicePanel
                customer={null}
                formHandlers={formHandlers}
                formPrefill={prefill}
                onNoteSaved={refresh}
              />
            ) : (
              <div className="p-6">
                <Alert variant="info">{t('assistant.voice.selectCustomer')}</Alert>
              </div>
            )
          }
        />
        )}
      </div>
    </div>
  );
}
