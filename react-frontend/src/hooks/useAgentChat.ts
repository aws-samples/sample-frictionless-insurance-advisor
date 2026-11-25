import { useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { v4 as uuidv4 } from 'uuid';

import { streamChat } from '../lib/agentcore';
import { chatErrorText } from '../lib/errors';
import { appendChunk, type AppEvent } from '../lib/sse';
import { parseSources } from '../lib/sources';
import type { ChatMessage, Customer, FormFillEvent, FormSchema } from '../types';

/**
 * What the chat needs from the application form that sits beside it. Held in a
 * ref so a new object on every render never restarts anything.
 */
export interface ChatFormBridge {
  /** The form open on screen with its current values, or null when none is. */
  getFormContext: () => { productType: string; values: Record<string, string> } | null;
  /** Field values the customer's record already supplies. */
  prefill: Record<string, string>;
  onFormOpen: (productType: string, schema: FormSchema) => void;
  onFormFill: (event: FormFillEvent) => void;
}

export interface UseAgentChatResult {
  messages: ChatMessage[];
  streaming: boolean;
  send: (prompt: string) => Promise<void>;
  reset: () => void;
  /** Session id of the chat on screen (changes when the chat is reset or another customer is selected). */
  sessionId: string;
  /** The unsent text in the message box of the chat on screen (empty when there is none). */
  draft: string;
  /** Set the unsent text for the chat on screen. An empty string drops it. */
  setDraft: (text: string) => void;
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
 * - The unsent text in the message box (the draft) is kept per customer the same way, so it
 *   survives the chat panel being unmounted (Home tab, logo) and never shows for another
 *   customer. In memory only: it can contain customer data, so it is never written to storage.
 */
export function useAgentChat(
  customer: Customer | null,
  formBridge?: ChatFormBridge
): UseAgentChatResult {
  const formBridgeRef = useRef<ChatFormBridge | undefined>(formBridge);
  formBridgeRef.current = formBridge;
  const customerId = customer?.customer_id ?? '__new_prospect__';
  // The language chosen in the language list (restored from localStorage by i18next). Every message
  // carries it, so the assistant answers in it; useTranslation re-renders on a switch, so the next
  // message of the same chat already uses the new one.
  const { i18n } = useTranslation();
  const language = baseLanguageCode(i18n.resolvedLanguage ?? i18n.language);

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

  // Unsent message-box text, by the same key as the chat entries (the prospect has its own key).
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const draft = drafts[customerId] ?? '';
  const setDraft = useCallback(
    (text: string) =>
      setDrafts((prev) => {
        if ((prev[customerId] ?? '') === text) return prev;
        const next = { ...prev };
        if (text === '') delete next[customerId];
        else next[customerId] = text;
        return next;
      }),
    [customerId]
  );

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
      // An error replaces the empty answer bubble, or follows the text already streamed.
      const showError = (text: string) =>
        updateActive((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last && last.role === 'assistant' && last.content === '') {
            next[next.length - 1] = { role: 'assistant', content: '', error: text };
          } else {
            next.push({ role: 'assistant', content: '', error: text });
          }
          return next;
        });

      const enrichedPrompt = buildPromptWithContext(trimmed, customer);
      const sessionId = entriesRef.current[customerId].sessionId;

      try {
        const bridge = formBridgeRef.current;
        const form = bridge?.getFormContext() ?? undefined;
        // True once a tool call has started since the last text chunk: the next
        // text is a new model turn and must not be glued to the previous one.
        let afterTool = false;
        for await (const item of streamChat({
          prompt: enrichedPrompt,
          customerId: customer?.customer_id ?? undefined,
          sessionId,
          form,
          formPrefill: bridge?.prefill,
          language,
        })) {
          if (item.kind === 'tool_use') {
            afterTool = true;
            continue;
          }
          if (item.kind === 'error') {
            // The runtime sends a code, not a sentence: show our own translated text for it.
            showError(chatErrorText(item.code, item.correlationId));
            continue;
          }
          if (item.kind === 'app_event') {
            if (item.event.type === 'sources') {
              // Sources belong to the answer being streamed, not to a form.
              const sources = parseSources(item.event.sources);
              if (sources.length > 0) {
                updateActive((prev) => {
                  const next = [...prev];
                  const last = next[next.length - 1];
                  if (last && last.role === 'assistant') {
                    next[next.length - 1] = { ...last, sources };
                  }
                  return next;
                });
              }
              continue;
            }
            if (item.event.type === 'guardrail_blocked') {
              // The compliance guardrail replaced the answer with its canned
              // sentence. Flag the bubble so the panel can show a clear,
              // translated message instead. A backend that does not send this
              // event leaves the canned sentence as the answer.
              // Side effect of a block: Strands rewrites the user's message in
              // AgentCore Memory to "[User input redacted.]", so the next turn
              // in this same chat starts from a redacted history.
              updateActive((prev) => {
                const next = [...prev];
                const last = next[next.length - 1];
                if (last && last.role === 'assistant') {
                  next[next.length - 1] = { ...last, guardrailBlocked: true };
                }
                return next;
              });
              continue;
            }
            applyAppEvent(item.event, formBridgeRef.current);
            continue;
          }
          const chunk = item.text;
          const joinAfterTool = afterTool;
          afterTool = false;
          updateActive((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last && last.role === 'assistant') {
              next[next.length - 1] = { ...last, content: appendChunk(last.content, chunk, joinAfterTool) };
            }
            return next;
          });
        }
      } catch (err) {
        // An ApiError carries a safe message; any other error here happened on this side.
        console.error('[chat] request failed', err);
        showError(err instanceof Error ? err.message : chatErrorText(undefined, undefined));
      } finally {
        setStreaming(false);
      }
    },
    [customer, customerId, language, streaming, updateActive]
  );

  const reset = useCallback(() => {
    entriesRef.current[customerId] = {
      messages: [],
      sessionId: makeSessionId(customerId),
    };
    setMessages([]);
  }, [customerId]);

  return { messages, streaming, send, reset, sessionId: activeEntry.sessionId, draft, setDraft };
}

/** Route one typed event from the agent's form tools to the form on screen. */
function applyAppEvent(event: AppEvent, bridge: ChatFormBridge | undefined): void {
  if (!bridge) return;
  if (event.type === 'form_open' && typeof event.productType === 'string' && event.schema) {
    bridge.onFormOpen(event.productType, event.schema as FormSchema);
  } else if (event.type === 'form_fill' && typeof event.path === 'string') {
    bridge.onFormFill({
      path: event.path,
      value: String(event.value ?? ''),
      confidence: typeof event.confidence === 'number' ? event.confidence : 0.6,
      source: typeof event.source === 'string' ? event.source : undefined,
    });
  }
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

/** `ko-KR` and `ko_KR` become `ko`: the agent knows the base codes of the language list. */
function baseLanguageCode(code: string | undefined): string | undefined {
  const base = code?.split(/[-_]/)[0]?.toLowerCase();
  return base || undefined;
}

function makeSessionId(customerId: string): string {
  return `react-${customerId}-${uuidv4()}`;
}
