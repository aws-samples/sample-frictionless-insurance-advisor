import { useCallback, useRef, useState } from 'react';
import { v4 as uuidv4 } from 'uuid';

import { streamChat } from '../lib/agentcore';
import type { ChatMessage, Customer } from '../types';

interface UseAgentChatResult {
  messages: ChatMessage[];
  streaming: boolean;
  send: (prompt: string) => Promise<void>;
  reset: () => void;
}

interface CustomerChatEntry {
  messages: ChatMessage[];
  sessionId: string;
}

/**
 * Per-customer chat state with streaming response accumulation.
 *
 * Behavior:
 * - Chat history and session ID are keyed by `customer.customer_id`, so
 *   switching between customers preserves each one's conversation.
 * - Every user message is prefixed with a short "hidden" context block
 *   (Customer name, customer_id, policy count) reminding the agent who
 *   the advisor is currently looking at. The header is built from the
 *   *currently selected* customer on every turn, so if the advisor
 *   switches profiles mid-chat, the next turn includes the new context.
 *   The raw user message is what's shown in the chat bubble.
 * - Session ID format: `react-<customerId>-<uuid>`. AgentCore Memory uses
 *   this as the session_id for the chat history.
 */
export function useAgentChat(customer: Customer | null): UseAgentChatResult {
  const customerId = customer?.customer_id ?? '__new_prospect__';

  // Per-customer chat state stored in a ref so switching customers doesn't
  // lose earlier conversations. We mirror the active customer's entry into
  // `messages` state so React re-renders.
  const entriesRef = useRef<Record<string, CustomerChatEntry>>({});
  if (!entriesRef.current[customerId]) {
    entriesRef.current[customerId] = {
      messages: [],
      sessionId: makeSessionId(customerId),
    };
  }
  const activeEntry = entriesRef.current[customerId];

  const [messages, setMessages] = useState<ChatMessage[]>(activeEntry.messages);
  const [streaming, setStreaming] = useState<boolean>(false);

  // When the selected customer changes, surface the new customer's messages.
  // We use a ref + a tracking state rather than useEffect to avoid an extra
  // render; the parent component remounts us whenever `customer` changes
  // reference, which is already the common case.
  const lastCustomerIdRef = useRef<string>(customerId);
  if (lastCustomerIdRef.current !== customerId) {
    lastCustomerIdRef.current = customerId;
    // eslint-disable-next-line react-hooks/rules-of-hooks -- setState inside
    // a render is safe when the value derived from props genuinely changes
    setMessages(entriesRef.current[customerId].messages);
  }

  const updateActive = useCallback(
    (updater: (prev: ChatMessage[]) => ChatMessage[]) => {
      const current = entriesRef.current[customerId].messages;
      const next = updater(current);
      entriesRef.current[customerId].messages = next;
      // Only push to React state if the currently selected customer is still
      // the one whose chat we just updated. (Prevents a race where the user
      // switches customers while a response is still streaming.)
      if (lastCustomerIdRef.current === customerId) {
        setMessages(next);
      }
    },
    [customerId]
  );

  const send = useCallback(
    async (prompt: string) => {
      const trimmed = prompt.trim();
      if (!trimmed || streaming) {
        return;
      }

      updateActive((prev) => [
        ...prev,
        { role: 'user', content: trimmed },
        { role: 'assistant', content: '' },
      ]);
      setStreaming(true);

      const enrichedPrompt = buildPromptWithContext(trimmed, customer);
      const sessionId = entriesRef.current[customerId].sessionId;

      try {
        for await (const chunk of streamChat({
          prompt: enrichedPrompt,
          customerId: customer?.customer_id ?? undefined,
          sessionId,
        })) {
          updateActive((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last && last.role === 'assistant') {
              next[next.length - 1] = { ...last, content: last.content + chunk };
            }
            return next;
          });
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        updateActive((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last && last.role === 'assistant' && last.content === '') {
            next[next.length - 1] = { role: 'assistant', content: '', error: message };
          } else {
            next.push({ role: 'assistant', content: '', error: message });
          }
          return next;
        });
      } finally {
        setStreaming(false);
      }
    },
    [customer, customerId, streaming, updateActive]
  );

  const reset = useCallback(() => {
    entriesRef.current[customerId] = {
      messages: [],
      sessionId: makeSessionId(customerId),
    };
    setMessages([]);
  }, [customerId]);

  return { messages, streaming, send, reset };
}

/**
 * Build the "hidden context" prompt that gets sent to the agent. The visible
 * chat bubble still shows only the raw user message; this enriched version
 * is what the agent receives so it always knows which customer is being
 * discussed and can adapt if the advisor switches profiles mid-conversation.
 */
function buildPromptWithContext(userMessage: string, customer: Customer | null): string {
  if (!customer) {
    return [
      'No customer selected — this is a new prospect onboarding session.',
      'The advisor wants to register a new prospect.',
      '',
      `Question: ${userMessage}`,
    ].join('\n');
  }
  const policyCount = customer.policies.length;
  const prospectNote = policyCount === 0 ? ' (this customer is a prospect — no policies yet)' : '';
  return [
    `Customer: ${customer.name} (customerId: ${customer.customer_id})${prospectNote}`,
    `Policies: ${policyCount}`,
    '',
    `Question: ${userMessage}`,
  ].join('\n');
}

function makeSessionId(customerId: string): string {
  return `react-${customerId}-${uuidv4()}`;
}
