import { ShieldAlert, ShieldCheck, ShieldX } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { ConsentNotice } from '../hooks/useVoiceChat';
import { fmtTime } from '../lib/format';
import type { ConsentStatus } from '../lib/voice';
import { Button } from '../ui';

interface ConsentBarProps {
  status: ConsentStatus;
  /** When consent was confirmed (ISO). */
  at: string | null;
  /** Customer being discussed, or null for a prospect. */
  customerName: string | null;
  notice: ConsentNotice;
  connected: boolean;
  onAnswer: (status: 'confirmed' | 'declined') => void;
  onStartNew: () => void;
}

/**
 * Recording consent. The assistant asks the question by voice;
 * the buttons answer it the same way. Until it is confirmed the runtime stores
 * no transcript, writes no memory and fills no forms.
 */
export function ConsentBar({ status, at, customerName, notice, connected, onAnswer, onStartNew }: ConsentBarProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;
  const who = customerName ?? t('assistant.voice.consent.theProspect');

  if (status === 'confirmed') {
    return (
      <div role="status" className="flex items-center gap-2 border-b border-success/30 bg-success/10 px-6 py-2 text-xs text-success">
        <ShieldCheck className="h-4 w-4 shrink-0" aria-hidden />
        <span>{t('assistant.voice.consent.confirmed', { time: fmtTime(at, locale) })}</span>
      </div>
    );
  }

  if (status === 'declined') {
    return (
      <div role="status" className="flex flex-wrap items-center gap-3 border-b border-danger/30 bg-danger/10 px-6 py-2 text-xs text-danger">
        <ShieldX className="h-4 w-4 shrink-0" aria-hidden />
        <span className="min-w-0 flex-1">{t('assistant.voice.consent.declinedMessage')}</span>
        <Button size="sm" variant="secondary" onClick={onStartNew}>
          {t('assistant.voice.consent.startNew')}
        </Button>
      </div>
    );
  }

  return (
    <div role="alert" className="flex flex-wrap items-center gap-3 border-b border-warning/30 bg-warning/10 px-6 py-2.5 text-xs">
      <ShieldAlert className="h-4 w-4 shrink-0 text-warning" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="font-medium text-foreground">{t('assistant.voice.consent.question', { name: who })}</div>
        <div className="text-foreground-muted">
          {notice === 'customer_changed' ? `${t('assistant.voice.consent.changed')} ` : ''}
          {t('assistant.voice.consent.hint')}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Button size="sm" variant="primary" disabled={!connected} onClick={() => onAnswer('confirmed')}>
          {t('assistant.voice.consent.confirm')}
        </Button>
        <Button size="sm" variant="secondary" disabled={!connected} onClick={() => onAnswer('declined')}>
          {t('assistant.voice.consent.decline')}
        </Button>
      </div>
    </div>
  );
}
