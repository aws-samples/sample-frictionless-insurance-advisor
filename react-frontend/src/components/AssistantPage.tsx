import { useMemo, useState } from 'react';

import { useTranslation } from 'react-i18next';

import type { ChatFormBridge } from '../hooks/useAgentChat';
import { useApplicationForm } from '../hooks/useApplicationForm';
import { useCustomers } from '../hooks/useCustomers';
import type { PriorityAction } from '../types';
import { Alert } from '../ui';

import { ChatPanel } from './ChatPanel';
import { CustomerPanel } from './CustomerPanel';
import { FormPanel } from './FormPanel';
import { Sidebar } from './Sidebar';
import { SidebarToggle } from './SidebarToggle';
import { SplitWorkspace } from './SplitWorkspace';

interface AssistantPageProps {
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
}

/**
 * Assistant page: sidebar (customer list) + resizable split of profile + chat.
 * This is the full advisor-experience surface, including the
 * "+ New Prospect" flow: when the sidebar's New Prospect button is clicked,
 * it calls onSelect(null), which puts the page in `prospectMode` and mounts
 * a ChatPanel with no customer context — the agent drives the onboarding
 * conversation and creates the DynamoDB profile through tool calls.
 */
export function AssistantPage({ sidebarCollapsed, onToggleSidebar }: AssistantPageProps) {
  const { t } = useTranslation();
  const { customers, loading, error, refresh } = useCustomers();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [prospectMode, setProspectMode] = useState<boolean>(false);
  // Set by the Cockpit: open this customer and send the matching starter prompt once.
  const [pendingStart, setPendingStart] = useState<{ customerId: string; action: PriorityAction } | null>(null);

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

  const handleOpenFromCockpit = (customerId: string, action: PriorityAction) => {
    setSelectedId(customerId);
    setProspectMode(false);
    setPendingStart({ customerId, action });
  };
  const selectedCustomer = useMemo(
    () => customers.find((c) => c.customer_id === selectedId) ?? null,
    [customers, selectedId]
  );

  // The form the assistant opens when the advisor asks for an application.
  // While one is open the left panel shows it instead of the profile.
  const form = useApplicationForm(selectedCustomer, prospectMode);
  const { activeForm } = form;
  const formBridge = useMemo<ChatFormBridge>(
    () => ({
      getFormContext: form.getFormContext,
      prefill: form.prefill,
      onFormOpen: form.openForm,
      onFormFill: form.fillField,
    }),
    [form.getFormContext, form.prefill, form.openForm, form.fillField]
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
                onOpenCustomer={handleOpenFromCockpit}
                onOpenForm={form.openForm}
              />
            )
          }
          right={
            selectedCustomer ? (
              <ChatPanel
                customer={selectedCustomer}
                formBridge={formBridge}
                pendingStart={pendingStart}
                onPendingStartDone={() => setPendingStart(null)}
              />
            ) : prospectMode ? (
              <ChatPanel customer={null} formBridge={formBridge} />
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
