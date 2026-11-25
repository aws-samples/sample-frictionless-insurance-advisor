import { useMemo } from 'react';

import { useTranslation } from 'react-i18next';

import { useAgentChat, type ChatFormBridge } from '../hooks/useAgentChat';
import { useApplicationForm } from '../hooks/useApplicationForm';
import { useCustomers } from '../hooks/useCustomers';
import { useSelection } from '../hooks/useSelection';
import { Alert } from '../ui';

import { ChatPanelView } from './ChatPanel';
import { CustomerPanel } from './CustomerPanel';
import { FormPanel } from './FormPanel';
import { NoCustomerState } from './NoCustomerState';
import { Sidebar } from './Sidebar';
import { SidebarToggle } from './SidebarToggle';
import { SplitWorkspace } from './SplitWorkspace';

interface AssistantPageProps {
  /** False while the page is kept mounted but hidden behind another tab. */
  active?: boolean;
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
}

/**
 * Text Assistant page: sidebar (customer list) + resizable split of profile + chat.
 * This is the full advisor-experience surface, including the
 * "+ New Prospect" flow: when the sidebar's New Prospect button is clicked,
 * it calls onSelect(null), which puts the page in `prospectMode` and mounts
 * a ChatPanel with no customer context — the agent drives the onboarding
 * conversation and creates the DynamoDB profile through tool calls.
 * With no customer and no prospect (the tab opened straight from Home) the main area asks
 * the advisor to pick a customer; the Cockpit is on the Home tab only.
 */
export function AssistantPage({ active = true, sidebarCollapsed, onToggleSidebar }: AssistantPageProps) {
  const { t } = useTranslation();
  const { customers, loading, error, refresh } = useCustomers();
  // Shared with the Voice page, so switching tab keeps the same customer.
  // The Cockpit hand-off (open this customer, send the starter prompt once) is held there too,
  // because the Cockpit lives on the Home tab and its buttons continue here.
  const {
    selectedId,
    prospectMode,
    select: handleSelect,
    pendingStart,
    clearPendingStart,
  } = useSelection();
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
  // The chat lives here, not in the chat panel: the panel is unmounted whenever no customer is
  // selected (the Home tab and the logo clear the selection), and every customer's history, and a
  // response still streaming, and each customer's unsent message, must survive that. Neither the Home tab nor
  // the logo remounts this page; a reload or signing out clears them.
  const chat = useAgentChat(selectedCustomer, formBridge);

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
          <NoCustomerState kind="chat" onNewProspect={() => handleSelect(null)} />
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
              <ChatPanelView
                customer={selectedCustomer}
                chat={chat}
                pendingStart={pendingStart}
                onPendingStartDone={clearPendingStart}
                active={active}
              />
            ) : prospectMode ? (
              <ChatPanelView customer={null} chat={chat} active={active} />
            ) : (
              <div className="p-6">
                <Alert variant="info">{t('assistant.chat.selectCustomer')}</Alert>
              </div>
            )
          }
        />
        )}
      </div>
    </div>
  );
}
