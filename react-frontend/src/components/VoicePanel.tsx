import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';

import {
  ChevronDown,
  MessageCircle,
  Mic,
  MicOff,
  Send,
  Sparkles,
  Sprout,
  Trash2,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { useVoiceChat } from '../hooks/useVoiceChat';
import { uploadDocument } from '../lib/api';
import { cn } from '../lib/cn';
import { Alert, Button, IconButton } from '../ui';
import type { Customer, UploadedDocument } from '../types';

import { DocumentAttachment } from './DocumentAttachment';
import { SamplePoliciesMenu } from './SamplePoliciesMenu';

interface VoicePanelProps {
  customer: Customer | null;
}

const CUSTOMER_PROMPT_KEYS = [
  'assistant.prompts.customer.policies',
  'assistant.prompts.customer.coverageGap',
  'assistant.prompts.customer.premiumDelta',
  'assistant.prompts.customer.claimsModel',
  'assistant.prompts.customer.incomeVsLumpSum',
] as const;

const PROSPECT_PROMPT_KEYS = [
  'assistant.prompts.prospect.firstProducts',
  'assistant.prompts.prospect.discovery',
  'assistant.prompts.prospect.bestProducts',
  'assistant.prompts.prospect.company',
  'assistant.prompts.prospect.whyUnicorn',
  'assistant.prompts.prospect.qualifyQuestions',
] as const;

export function VoicePanel({ customer }: VoicePanelProps) {
  const { t } = useTranslation();
  const {
    connected,
    recording,
    speaking,
    error,
    partialUser,
    partialAssistant,
    history,
    startRecording,
    stopRecording,
    sendText,
    clearHistory,
    clearError,
  } = useVoiceChat(customer);

  const [input, setInput] = useState<string>('');
  const [attachment, setAttachment] = useState<UploadedDocument | null>(null);
  const [quickQuestionsOpen, setQuickQuestionsOpen] = useState<boolean>(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const messagesPresent =
    history.length > 0 || partialUser.length > 0 || partialAssistant.length > 0;

  const isProspect = customer === null || customer.policies.length === 0;
  const prompts = useMemo(() => {
    const keys = isProspect ? PROSPECT_PROMPT_KEYS : CUSTOMER_PROMPT_KEYS;
    return keys.map((key) => ({ key, text: t(key) }));
  }, [isProspect, t]);

  const HeaderIcon = isProspect ? Sprout : MessageCircle;
  const headerText = isProspect
    ? t('assistant.chat.headingProspect')
    : t('assistant.chat.headingCustomer');

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [history, partialUser, partialAssistant]);

  const toggleVoice = () => {
    if (!connected) return;
    if (recording) {
      stopRecording();
    } else {
      void startRecording();
    }
  };

  const handleSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const text = input.trim();
    if (!connected) return;
    if (!text && (!attachment || attachment.status !== 'ready')) return;
    if (attachment?.status === 'uploading') return;

    setInput('');
    const sentAttachment = attachment;
    setAttachment(null);

    const finalText = sentAttachment?.status === 'ready'
      ? buildVoiceTextWithAttachment(text, sentAttachment)
      : text;
    sendText(finalText);
  };

  const handleAttach = async (file: File) => {
    setAttachment({
      document_id: '',
      filename: file.name,
      content_type: file.type,
      size_bytes: file.size,
      status: 'uploading',
    });
    try {
      const result = await uploadDocument(file, customer?.customer_id ?? null);
      const ready: UploadedDocument = {
        document_id: result.document_id,
        filename: result.filename,
        content_type: result.content_type,
        size_bytes: result.size_bytes,
        status: 'ready',
      };
      setAttachment(ready);
      // Auto-send so the voice agent picks up the document immediately —
      // the advisor doesn't need to type anything to get the extraction
      // started. They can then confirm verbally.
      if (connected) {
        sendText(buildVoiceTextWithAttachment('', ready));
        setAttachment(null);
      }
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

  const statusLabel = !connected
    ? t('assistant.voice.status.connecting')
    : speaking
      ? t('assistant.voice.status.speaking')
      : recording
        ? t('assistant.voice.status.listening')
        : t('assistant.voice.status.connected');

  // Map status to a Badge variant + dot color. Listening / speaking use the
  // brand pulse class; the dot itself gets a stronger color.
  const statusVariant: 'info' | 'success' | 'warning' | 'brand' = !connected
    ? 'info'
    : speaking
      ? 'brand'
      : recording
        ? 'warning'
        : 'success';

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 border-b border-border px-6 py-3">
        <div className="inline-flex items-center gap-2">
          <HeaderIcon className="h-4 w-4 text-brand-2" />
          <h4 className="text-sm font-semibold">{headerText}</h4>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={cn(
              'relative inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium',
              statusVariant === 'success' && 'border-success/30 bg-success/10 text-success',
              statusVariant === 'warning' && 'border-warning/30 bg-warning/10 text-warning',
              statusVariant === 'info' && 'border-info/30 bg-info/10 text-info',
              statusVariant === 'brand' && 'border-brand-2/30 bg-brand-2/10 text-brand-2'
            )}
          >
            <span
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                statusVariant === 'success' && 'bg-success',
                statusVariant === 'warning' && 'bg-warning',
                statusVariant === 'info' && 'bg-info',
                statusVariant === 'brand' && 'bg-brand-2',
                (recording || speaking) && 'neon-pulse'
              )}
            />
            {statusLabel}
          </span>
          {messagesPresent ? (
            <IconButton
              aria-label={t('common.actions.clear')}
              variant="ghost"
              size="sm"
              onClick={clearHistory}
              disabled={!connected}
            >
              <Trash2 className="h-4 w-4" />
            </IconButton>
          ) : null}
        </div>
      </div>

      {error ? (
        <div className="px-6 pt-3">
          <Alert variant="danger" onDismiss={clearError}>
            {error}
          </Alert>
        </div>
      ) : null}

      {/* Voice control bar */}
      <div className="border-b border-border px-6 py-3">
        <Button
          variant={recording ? 'destructive' : 'primary'}
          size="md"
          onClick={toggleVoice}
          disabled={!connected}
        >
          {recording ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
          {recording
            ? t('assistant.voice.stopVoice')
            : t('assistant.voice.startVoice')}
        </Button>
      </div>

      {/* Transcript */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-4">
        {history.length === 0 && !partialUser && !partialAssistant ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center text-sm text-foreground-muted">
            <Sparkles className="h-6 w-6 text-brand-2" />
            <p className="italic">
              {connected
                ? t('assistant.chat.emptyPlaceholder')
                : t('assistant.voice.emptyConnecting')}
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {history.map((turn) => {
              if (turn.role === 'user') {
                return (
                  <div key={turn.id} className="flex justify-end">
                    <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-gradient-to-br from-brand-2 to-brand-1 px-4 py-2 text-sm text-white shadow-[var(--shadow-sm)]">
                      <div className="text-[10px] font-medium uppercase tracking-wide opacity-80">
                        {t('assistant.chat.userBubbleLabel')}
                      </div>
                      <div className="mt-0.5 whitespace-pre-wrap">{turn.text}</div>
                    </div>
                  </div>
                );
              }
              return (
                <div key={turn.id} className="flex justify-start">
                  <div className="max-w-[92%] rounded-2xl rounded-bl-sm border border-border bg-background-elevated px-4 py-2.5 text-sm text-foreground shadow-[var(--shadow-sm)]">
                    <div className="text-[10px] font-medium uppercase tracking-wide text-foreground-muted">
                      {t('assistant.chat.assistantBubbleLabel')}
                    </div>
                    <div className="mt-0.5 whitespace-pre-wrap">{turn.text}</div>
                  </div>
                </div>
              );
            })}

            {partialUser ? (
              <div className="flex justify-end">
                <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-gradient-to-br from-brand-2/70 to-brand-1/70 px-4 py-2 text-sm italic text-white shadow-[var(--shadow-sm)] opacity-80">
                  <div className="text-[10px] font-medium uppercase tracking-wide opacity-80">
                    {t('assistant.chat.userBubbleLabel')}
                  </div>
                  <div className="mt-0.5">{partialUser}</div>
                </div>
              </div>
            ) : null}
            {partialAssistant ? (
              <div className="flex justify-start">
                <div className="max-w-[92%] rounded-2xl rounded-bl-sm border border-border bg-background-elevated px-4 py-2.5 text-sm italic text-foreground-muted shadow-[var(--shadow-sm)] opacity-90">
                  <div className="text-[10px] font-medium uppercase tracking-wide">
                    {t('assistant.chat.assistantBubbleLabel')}
                  </div>
                  <div className="mt-0.5 whitespace-pre-wrap">{partialAssistant}</div>
                </div>
              </div>
            ) : null}
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
            disabled={!connected}
          />
          <input
            type="text"
            aria-label={t('assistant.chat.messageLabel')}
            placeholder={
              attachment?.status === 'ready'
                ? t('assistant.chat.placeholderWithAttachment')
                : connected
                  ? t('assistant.voice.typePlaceholder')
                  : t('assistant.voice.connecting')
            }
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={!connected}
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
            disabled={
              !connected ||
              attachment?.status === 'uploading' ||
              (!input.trim() && attachment?.status !== 'ready')
            }
            aria-label={t('common.actions.send')}
          >
            <Send className="h-4 w-4" />
            <span className="hidden sm:inline">{t('common.actions.send')}</span>
          </Button>
        </div>
        <div className="mt-2 flex items-center justify-between gap-2">
          <p className="text-[11px] text-foreground-muted">
            {recording
              ? t('assistant.voice.hintVoice')
              : t('assistant.voice.hintText')}
          </p>
          <SamplePoliciesMenu customer={customer} />
        </div>
      </form>

      {/* Quick prompts — collapsed by default to save vertical space. */}
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
            {prompts.map((prompt) => (
              <button
                key={prompt.key}
                type="button"
                onClick={() => sendText(prompt.text)}
                disabled={!connected}
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
 * Build the synthetic transcript text the voice agent receives when the
 * advisor uploads a document. We include the document_id and filename so
 * the agent can immediately call extract_policy_from_document.
 */
function buildVoiceTextWithAttachment(typedText: string, doc: UploadedDocument): string {
  const fallback = 'I attached an insurance policy document. Please extract the fields and read me a quick summary.';
  const userPart = typedText || fallback;
  return [
    `[Attached document: ${doc.filename} (document_id: ${doc.document_id})]`,
    '',
    userPart,
  ].join('\n');
}
