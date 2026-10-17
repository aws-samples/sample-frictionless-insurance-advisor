import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useApplicationForm } from '../hooks/useApplicationForm';
import { useCustomers } from '../hooks/useCustomers';
import type { VoiceConsentHandlers, VoiceFormHandlers } from '../hooks/useVoiceChat';
import { addNote } from '../lib/api';
import { Alert } from '../ui';
import { CustomerPanel } from './CustomerPanel';
import { FormPanel } from './FormPanel';
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
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [prospectMode, setProspectMode] = useState<boolean>(false);
  const handleHome = () => {
    setSelectedId(null);
    setProspectMode(false);
  };
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

  const form = useApplicationForm(selectedCustomer, prospectMode);
  const { activeForm, prefill } = form;

  const totalPolicies = useMemo(
    () => customers.reduce((sum, c) => sum + c.policies.length, 0),
    [customers]
  );

  const formHandlers = useMemo<VoiceFormHandlers>(
    () => ({ onFormOpen: form.openForm, onFormFill: form.fillField }),
    [form.openForm, form.fillField]
  );

  // Log the advisor's consent confirmation as a note on the customer, through
  // the notes API. A failure is shown nowhere else: the consent itself
  // stands in the transcript header, and the note can be added by hand.
  const consentHandlers = useMemo<VoiceConsentHandlers>(
    () => ({
      onConsentConfirmed: (customerId, at) => {
        const when = at.replace('T', ' ').replace(/(:\d\d)?(\+00:00|Z)$/, '') + ' UTC';
        void addNote(customerId, {
          type: 'interaction',
          text: `Recording consent confirmed ${when} (voice session). The customer agreed to the conversation being transcribed.`,
        })
          .then(() => refresh())
          .catch((err: unknown) => console.error('[consent-note]', err));
      },
    }),
    [refresh]
  );
  return (
    <div className="relative flex flex-1 w-full overflow-hidden min-h-0">
      {sidebarCollapsed ? null : (
        <Sidebar
          customers={customers}
          selectedId={selectedId}
          loading={loading}
          error={error}
          homeActive={selectedId === null && !prospectMode}
          onHome={handleHome}
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
              />
            ) : (
              <CustomerPanel
                customer={selectedCustomer}
                totalCustomers={customers.length}
                totalPolicies={totalPolicies}
                // On the Voice page the Cockpit just opens the customer; the starter
                // prompts are for the text chat.
                onOpenCustomer={(id) => handleSelect(id)}
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
                consentHandlers={consentHandlers}
                onNoteSaved={refresh}
              />
            ) : prospectMode ? (
              <VoicePanel
                customer={null}
                formHandlers={formHandlers}
                formPrefill={prefill}
                consentHandlers={consentHandlers}
                onNoteSaved={refresh}
              />
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
