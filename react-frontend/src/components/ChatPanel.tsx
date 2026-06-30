import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';

import { ChevronDown, MessageCircle, Send, Sparkles, Sprout, Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { useAgentChat } from '../hooks/useAgentChat';
import { uploadDocument } from '../lib/api';
import { cn } from '../lib/cn';
import { Alert, Button, IconButton, Spinner } from '../ui';
import type { Customer, UploadedDocument } from '../types';

import { DocumentAttachment } from './DocumentAttachment';
import { MarkdownMessage } from './MarkdownMessage';
import { SamplePoliciesMenu } from './SamplePoliciesMenu';

interface ChatPanelProps {
  customer: Customer | null;
}

// Static key tables — declared as literal arrays so the typed `t()` can
// narrow each string to a known key in the assistant namespace.
//
// Customer-mode chips: two customer-context starters (policies, coverageGap)
// plus three of the strongest advisor-vs-competitor questions from the
// Sample questions brief (premium delta, claims model, income replacement
// vs lump-sum). Anything else from the brief lives in the collapsible
// "more questions" section below the chip rail so the default UI stays
// uncluttered.
const CUSTOMER_PROMPT_KEYS = [
  'assistant.prompts.customer.policies',
  'assistant.prompts.customer.coverageGap',
  'assistant.prompts.customer.premiumDelta',
  'assistant.prompts.customer.claimsModel',
  'assistant.prompts.customer.incomeVsLumpSum',
] as const;

// Customer-mode "more questions" — the four advisor-vs-competitor questions
// from the brief that aren't already in the chip rail above. These render
// in a collapsible section so a presenter can show the full question set
// on demand without crowding the default UI.
const CUSTOMER_MORE_PROMPT_KEYS = [
  'assistant.prompts.customer.mentalHealthMaternity',
  'assistant.prompts.customer.outpatientDentalOptical',
  'assistant.prompts.customer.dayOneCover',
  'assistant.prompts.customer.preExistingWaiting',
] as const;

const PROSPECT_PROMPT_KEYS = [
  'assistant.prompts.prospect.firstProducts',
  'assistant.prompts.prospect.discovery',
  'assistant.prompts.prospect.bestProducts',
  'assistant.prompts.prospect.company',
  'assistant.prompts.prospect.whyUnicorn',
  'assistant.prompts.prospect.qualifyQuestions',
] as const;

export function ChatPanel({ customer }: ChatPanelProps) {
  const { t } = useTranslation();
  const { messages, send, streaming, reset } = useAgentChat(customer);
  const [input, setInput] = useState<string>('');
  const [attachment, setAttachment] = useState<UploadedDocument | null>(null);
  const [quickQuestionsOpen, setQuickQuestionsOpen] = useState<boolean>(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const isProspect = customer === null || customer.policies.every((p) => p.third_party);

  const prompts = useMemo(() => {
    const keys = isProspect ? PROSPECT_PROMPT_KEYS : CUSTOMER_PROMPT_KEYS;
    return keys.map((key) => ({ key, text: t(key) }));
  }, [isProspect, t]);

  // Extra advisor-vs-competitor questions for customer mode only. Empty in
  // prospect mode — the prospect chip rail is already focused on onboarding.
  const morePrompts = useMemo(() => {
    if (isProspect) return [];
    return CUSTOMER_MORE_PROMPT_KEYS.map((key) => ({ key, text: t(key) }));
  }, [isProspect, t]);

  const HeaderIcon = isProspect ? Sprout : MessageCircle;
  const headerText = isProspect
    ? t('assistant.chat.headingProspect')
    : t('assistant.chat.headingCustomer');

  // Auto-scroll the message stream as new content streams in.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, streaming]);

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const prompt = input.trim();
    // Allow sending with just an attachment (no typed prompt) — the agent's
    // system prompt understands document_id references on their own.
    if (!prompt && (!attachment || attachment.status !== 'ready')) return;
    if (attachment?.status === 'uploading') return;

    setInput('');
    const sentAttachment = attachment;
    setAttachment(null);

    const finalPrompt = sentAttachment?.status === 'ready'
      ? buildPromptWithAttachment(prompt, sentAttachment)
      : prompt;
    await send(finalPrompt);
  };

  const handleAttach = async (file: File) => {
    // Optimistically show the chip as 'uploading' so the user sees feedback
    // immediately. The backend issues a presigned URL and the browser PUTs
    // the binary directly to S3.
    setAttachment({
      document_id: '',
      filename: file.name,
      content_type: file.type,
      size_bytes: file.size,
      status: 'uploading',
    });
    try {
      const result = await uploadDocument(file, customer?.customer_id ?? null);
      setAttachment({
        document_id: result.document_id,
        filename: result.filename,
        content_type: result.content_type,
        size_bytes: result.size_bytes,
        status: 'ready',
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setAttachment({
        document_id: '',
        filename: file.name,
        content_type: file.type,
        size_bytes: file.size,
        status: 'error',
        error: message,
      });
    }
  };

  const handleRemoveAttachment = () => {
    setAttachment(null);
  };

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 border-b border-border px-6 py-3">
        <div className="inline-flex items-center gap-2">
          <HeaderIcon className="h-4 w-4 text-brand-2" />
          <h4 className="text-sm font-semibold">{headerText}</h4>
        </div>
        {messages.length > 0 ? (
          <IconButton
            aria-label={t('common.actions.clear')}
            variant="ghost"
            size="sm"
            onClick={reset}
            disabled={streaming}
          >
            <Trash2 className="h-4 w-4" />
          </IconButton>
        ) : null}
      </div>

      {/* Messages */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-6 py-4"
      >
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center text-sm text-foreground-muted">
            <Sparkles className="h-6 w-6 text-brand-2" />
            <p className="italic">{t('assistant.chat.emptyPlaceholder')}</p>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {messages.map((message, index) => {
              if (message.role === 'user') {
                return (
                  <div key={index} className="flex justify-end">
                    <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-gradient-to-br from-brand-2 to-brand-1 px-4 py-2 text-sm text-white shadow-[var(--shadow-sm)]">
                      <div className="text-[10px] font-medium uppercase tracking-wide opacity-80">
                        {t('assistant.chat.userBubbleLabel')}
                      </div>
                      <div className="mt-0.5 whitespace-pre-wrap">{message.content}</div>
                    </div>
                  </div>
                );
              }
              if (message.error) {
                return (
                  <Alert key={index} variant="danger">
                    {message.error}
                  </Alert>
                );
              }
              return (
                <div key={index} className="flex justify-start">
                  <div className="max-w-[92%] rounded-2xl rounded-bl-sm border border-border bg-background-elevated px-4 py-2.5 text-sm text-foreground shadow-[var(--shadow-sm)]">
                    <div className="text-[10px] font-medium uppercase tracking-wide text-foreground-muted">
                      {t('assistant.chat.assistantBubbleLabel')}
                    </div>
                    <div className="mt-0.5">
                      {message.content ? (
                        <MarkdownMessage content={message.content} />
                      ) : streaming ? (
                        <span className="inline-flex items-center gap-2 italic text-foreground-muted">
                          <Spinner size="sm" />
                          {t('assistant.chat.thinking')}
                        </span>
                      ) : null}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Composer */}
      <form
        onSubmit={handleSubmit}
        className="border-t border-border px-6 py-3"
      >
        <div className="flex items-center gap-2">
          <DocumentAttachment
            attachment={attachment}
            onAttach={handleAttach}
            onRemove={handleRemoveAttachment}
            disabled={streaming}
          />
          <input
            type="text"
            aria-label={t('assistant.chat.messageLabel')}
            placeholder={
              attachment?.status === 'ready'
                ? t('assistant.chat.placeholderWithAttachment')
                : isProspect
                  ? t('assistant.chat.placeholderProspect')
                  : t('assistant.chat.placeholderCustomer')
            }
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={streaming}
            className={cn(
              'h-10 flex-1 rounded-md border border-border bg-background-elevated px-3 text-sm placeholder:text-foreground-muted/70',
              'focus-visible:outline-none focus-visible:border-ring/60 focus-visible:ring-2 focus-visible:ring-ring/30',
              'disabled:opacity-50'
            )}
          />
          <Button
            type="submit"
            variant="primary"
            size="md"
            loading={streaming}
            disabled={
              streaming ||
              attachment?.status === 'uploading' ||
              (!input.trim() && attachment?.status !== 'ready')
            }
            aria-label={t('common.actions.send')}
          >
            <Send className="h-4 w-4" />
            <span className="hidden sm:inline">{t('common.actions.send')}</span>
          </Button>
        </div>
        <div className="mt-2">
          <SamplePoliciesMenu customer={customer} />
        </div>
      </form>

      {/* Quick prompts — collapsed by default to save vertical space.
          Toggling reveals the full prompt set: the primary questions plus
          the extra advisor-vs-competitor questions in customer mode. */}
      <div className="border-t border-border bg-background-muted/40 px-6 py-3">
        <button
          type="button"
          onClick={() => setQuickQuestionsOpen((open) => !open)}
          aria-expanded={quickQuestionsOpen}
          className={cn(
            'inline-flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-foreground-muted',
            'transition-colors hover:text-foreground'
          )}
        >
          <ChevronDown
            className={cn(
              'h-3 w-3 transition-transform duration-150',
              quickQuestionsOpen ? 'rotate-0' : '-rotate-90'
            )}
          />
          {t('assistant.chat.quickQuestions')}
        </button>
        {quickQuestionsOpen ? (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {[...prompts, ...morePrompts].map((prompt) => (
              <button
                key={prompt.key}
                type="button"
                onClick={() => void send(prompt.text)}
                disabled={streaming}
                className={cn(
                  'rounded-full border border-border bg-background-elevated px-3 py-1 text-xs text-foreground-muted',
                  'transition-[background,color,border-color] duration-150',
                  'hover:text-foreground hover:border-brand-2/50 hover:bg-brand-2/5',
                  'disabled:opacity-50 disabled:cursor-not-allowed'
                )}
              >
                {prompt.text}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}


/**
 * Wrap the user's typed message with a structured note about the attached
 * document. The agent's system prompt teaches it to look for `document_id`
 * and call extract_policy_from_document. We deliberately put the
 * document_id on its own line and include the filename so the agent has
 * all the context it needs.
 *
 * If the user typed nothing, we generate a default request to extract,
 * which is the most common intent when uploading a third-party policy PDF.
 */
function buildPromptWithAttachment(typedPrompt: string, doc: UploadedDocument): string {
  const fallback = 'Extract the policy from this document and walk me through what you found.';
  const userPart = typedPrompt || fallback;
  return [
    `[Attached document: ${doc.filename} (document_id: ${doc.document_id})]`,
    '',
    userPart,
  ].join('\n');
}
