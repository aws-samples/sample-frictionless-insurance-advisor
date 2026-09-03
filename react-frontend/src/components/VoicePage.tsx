import { useCallback, useMemo, useState } from 'react';

import { useTranslation } from 'react-i18next';

import { useCustomers } from '../hooks/useCustomers';
import { useFormFill, type FormPrefill } from '../hooks/useFormFill';
import type { VoiceFormHandlers } from '../hooks/useVoiceChat';
import { Alert } from '../ui';
import type { Customer, FormSchema } from '../types';

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
 * Fallback schema shape used only while no form is open, so the fill hook has
 * something to compute progress against. Replaced by the real schema the
 * agent fetches from `get_form_schema` the moment an application starts.
 */
const EMPTY_SCHEMA: FormSchema = {
  form_id: 'none',
  product_type: 'none',
  product_name: '',
  title: '',
  sections: [],
};

/**
 * Seed the form with what the CRM already knows about the customer, so the
 * advisor never dictates details we hold on file. Only the fields the profile
 * can actually answer — the rest come from the conversation.
 */
function prefillFromCustomer(customer: Customer | null): FormPrefill {
  if (!customer) return {};
  const prefill: FormPrefill = {};
  const put = (path: string, value: string | number | boolean | undefined | null) => {
    if (value === undefined || value === null || value === '') return;
    prefill[path] = String(value);
  };

  put('applicant.full_name', customer.name);
  put('applicant.date_of_birth', customer.date_of_birth);
  put('applicant.occupation', customer.occupation);
  put('applicant.annual_income', customer.annual_income);
  put('applicant.email', customer.email);
  put('applicant.phone', customer.phone);
  put('applicant.address', customer.address);
  put('health.smoker', customer.smoking);
  put('health.existing_conditions', customer.medical_conditions);

  return prefill;
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
  // Set when the agent calls its `open_application_form` tool.
  const [activeForm, setActiveForm] = useState<FormSchema | null>(null);

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

  const prefill = useMemo(() => prefillFromCustomer(selectedCustomer), [selectedCustomer]);

  const {
    fields,
    progress,
    applyFill,
    setFieldManually,
    confirmField,
    carriedOver,
    noteSchemaChange,
  } = useFormFill(activeForm ?? EMPTY_SCHEMA, prefill);

  const totalPolicies = useMemo(
    () => customers.reduce((sum, c) => sum + c.policies.length, 0),
    [customers]
  );

  const formHandlers = useMemo<VoiceFormHandlers>(
    () => ({
      onFormOpen: (_productType, schema) => {
        // Deliberately does NOT clear existing answers. If the conversation
        // moves from term life to whole life, the applicant, health and
        // beneficiary questions are the same — making the advisor cover them
        // again would be the wrong behaviour. Only product-specific fields
        // start empty.
        noteSchemaChange(schema);
        setActiveForm(schema);
      },
      onFormFill: applyFill,
    }),
    [applyFill, noteSchemaChange]
  );

  const closeForm = useCallback(() => setActiveForm(null), []);

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
            activeForm ? (
              <FormPanel
                schema={activeForm}
                fields={fields}
                progress={progress}
                carriedOver={carriedOver}
                onFieldChange={setFieldManually}
                onConfirmField={confirmField}
                onClose={closeForm}
              />
            ) : (
              <CustomerPanel
                customer={selectedCustomer}
                totalCustomers={customers.length}
                totalPolicies={totalPolicies}
              />
            )
          }
          right={
            selectedCustomer ? (
              <VoicePanel
                customer={selectedCustomer}
                formHandlers={formHandlers}
                formPrefill={prefill}
              />
            ) : prospectMode ? (
              <VoicePanel customer={null} formHandlers={formHandlers} formPrefill={prefill} />
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
