import { Keyboard, Mic, RefreshCw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { fmtDateTime, fmtTime } from '../lib/format';
import { Badge, Card } from '../ui';

/*
 * Chat-style view of one saved voice transcript. Read-only. The data is what
 * the voice runtime wrote (voice-agent/transcripts.py) and is read defensively
 * since it is JSON from storage.
 */

interface Turn {
  t?: string;
  role?: string;
  mode?: string;
  text?: string;
  customer?: string | null;
}
export interface TranscriptDoc {
  started_at?: string;
  updated_at?: string;
  consent_confirmed_at?: string | null;
  ended?: boolean;
  turns: Turn[];
}

/** The transcript in a Data-tab payload, or null if this JSON is something else. */
export function parseTranscript(content: string): TranscriptDoc | null {
  try {
    const p: unknown = JSON.parse(content);
    if (p && typeof p === 'object' && Array.isArray((p as TranscriptDoc).turns)) {
      return p as TranscriptDoc;
    }
  } catch {
    /* not JSON */
  }
  return null;
}

export function TranscriptView({ data, onRefresh }: { data: TranscriptDoc; onRefresh?: () => void }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;
  const turns = data.turns.filter((x) => typeof x.text === 'string' && x.text.trim() !== '');
  const inProgress = data.ended === false;
  let lastCustomer: string | null | undefined;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <Card feature className="p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={inProgress ? 'success' : 'neutral'} dot={inProgress}>
            {inProgress ? t('assistant.pages.data.transcript.inProgress') : t('assistant.pages.data.transcript.ended')}
          </Badge>
          <span className="text-xs text-foreground-muted">
            {t('assistant.pages.data.transcript.started', { time: fmtDateTime(data.started_at, locale) })}
          </span>
          {onRefresh ? (
            <button
              type="button"
              onClick={onRefresh}
              className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-border bg-background-elevated px-2.5 py-1 text-xs font-medium hover:bg-background-muted"
            >
              <RefreshCw className="h-3 w-3" aria-hidden />
              {t('assistant.pages.data.transcript.refresh')}
            </button>
          ) : null}
        </div>
        <p className="mt-2 text-xs text-foreground-muted">
          {t('assistant.pages.data.transcript.expires', { time: fmtDateTime(data.updated_at, locale) })}
        </p>
        {data.consent_confirmed_at ? (
          <p className="mt-1 text-xs text-foreground-muted">
            {t('assistant.pages.data.transcript.consent', { time: fmtDateTime(data.consent_confirmed_at, locale) })}
          </p>
        ) : null}
      </Card>

      {turns.length === 0 ? (
        <p className="text-sm italic text-foreground-muted">{t('assistant.pages.data.transcript.empty')}</p>
      ) : (
        <ol className="flex flex-col gap-2.5">
          {turns.map((turn, i) => {
            const isAdvisor = turn.role === 'advisor';
            const customerChanged = turn.customer !== lastCustomer;
            lastCustomer = turn.customer;
            return (
              <li key={i} className="flex flex-col gap-1">
                {customerChanged ? (
                  <div className="text-center text-[11px] uppercase tracking-wide text-foreground-muted">
                    {turn.customer
                      ? t('assistant.pages.data.transcript.about', { name: turn.customer })
                      : t('assistant.pages.data.transcript.aboutProspect')}
                  </div>
                ) : null}
                <div className={isAdvisor ? 'flex justify-end' : 'flex justify-start'}>
                  <div
                    className={
                      isAdvisor
                        ? 'max-w-[85%] rounded-2xl rounded-br-sm bg-gradient-to-br from-brand-2 to-brand-1 px-4 py-2 text-sm text-white shadow-[var(--shadow-sm)]'
                        : 'max-w-[85%] rounded-2xl rounded-bl-sm border border-border bg-background-elevated px-4 py-2 text-sm text-foreground shadow-[var(--shadow-sm)]'
                    }
                  >
                    <div
                      className={
                        'flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide ' +
                        (isAdvisor ? 'opacity-80' : 'text-foreground-muted')
                      }
                    >
                      {isAdvisor ? t('assistant.pages.data.transcript.advisor') : t('assistant.pages.data.transcript.assistant')}
                      {turn.mode === 'typed' ? (
                        <span className="inline-flex items-center gap-1" title={t('assistant.pages.data.transcript.typed')}>
                          <Keyboard className="h-3 w-3" aria-hidden />
                          {t('assistant.pages.data.transcript.typed')}
                        </span>
                      ) : (
                        <Mic className="h-3 w-3" aria-hidden />
                      )}
                      <span className="ml-auto font-normal normal-case tabular-nums">{fmtTime(turn.t, locale)}</span>
                    </div>
                    <div className="mt-0.5 whitespace-pre-wrap">{turn.text}</div>
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
