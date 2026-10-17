import { useEffect, useRef, useState } from 'react';
import { FileText, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { addNote, friendlyApiError, summariseMeeting } from '../lib/api';
import { cn } from '../lib/cn';
import { Alert, Button, IconButton, Spinner } from '../ui';
import type { Customer, VoiceMessage } from '../types';

// Mirrors NOTE_TEXT_MAX in lambda/profile; the server enforces it.
const NOTE_TEXT_MAX = 1000;

interface MeetingNotePanelProps {
  customer: Customer;
  turns: VoiceMessage[];
  onClose: () => void;
  /** Called after the note is saved, so the page can refresh the customer. */
  onSaved: () => void;
}

type Phase = 'loading' | 'ready' | 'insufficient' | 'error' | 'saved';

/**
 * Draft a meeting note from the conversation on screen, let the advisor edit
 * it, and save it. The draft comes from lambda/meeting_summary; saving uses
 * the ordinary notes route as the advisor, so the assistant never writes it.
 */
export function MeetingNotePanel({ customer, turns, onClose, onSaved }: MeetingNotePanelProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;
  const [phase, setPhase] = useState<Phase>('loading');
  const [text, setText] = useState<string>('');
  const [date, setDate] = useState<string>('');
  const [trimmed, setTrimmed] = useState<boolean>(false);
  const [error, setError] = useState<string>('');
  const [saving, setSaving] = useState<boolean>(false);
  // The panel summarises the conversation as it was when it opened.
  const turnsRef = useRef(turns);

  useEffect(() => {
    let cancelled = false;
    summariseMeeting(
      customer.customer_id,
      turnsRef.current.map((m) => ({ role: m.role === 'user' ? 'user' : 'assistant', text: m.text })),
      locale
    )
      .then((res) => {
        if (cancelled) return;
        if (res.insufficient) {
          setPhase('insufficient');
          return;
        }
        setText(res.note_text);
        setDate(res.follow_up_date);
        setTrimmed(res.truncated);
        setPhase('ready');
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(friendlyApiError(err));
        setPhase('error');
      });
    return () => {
      cancelled = true;
    };
  }, [customer.customer_id, locale]);

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      await addNote(customer.customer_id, {
        type: 'interaction',
        text: text.trim(),
        ...(date ? { event_date: date } : {}),
      });
      setPhase('saved');
      onSaved();
    } catch (err) {
      setError(friendlyApiError(err));
    } finally {
      setSaving(false);
    }
  };

  const inputClass =
    'w-full rounded-lg border border-border bg-background px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-brand-2/40';

  return (
    <div className="border-b border-border bg-background-muted/40 px-6 py-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="inline-flex items-center gap-2">
          <FileText className="h-4 w-4 text-brand-2" />
          <h4 className="text-sm font-semibold">{t('assistant.meetingNote.title')}</h4>
        </div>
        <IconButton aria-label={t('common.actions.close')} variant="ghost" size="sm" onClick={onClose}>
          <X className="h-4 w-4" />
        </IconButton>
      </div>

      {phase === 'loading' ? (
        <div className="flex items-center gap-2 py-2 text-sm text-foreground-muted">
          <Spinner size="sm" />
          {t('assistant.meetingNote.loading')}
        </div>
      ) : null}

      {phase === 'insufficient' ? (
        <p className="py-2 text-sm text-foreground-muted">{t('assistant.meetingNote.insufficient')}</p>
      ) : null}

      {phase === 'error' ? (
        <Alert variant="danger">{t('assistant.meetingNote.error', { message: error })}</Alert>
      ) : null}

      {phase === 'saved' ? (
        <Alert variant="success">{t('assistant.meetingNote.saved')}</Alert>
      ) : null}

      {phase === 'ready' ? (
        <div className="space-y-3">
          <p className="text-xs text-foreground-muted">{t('assistant.meetingNote.intro')}</p>
          <div>
            <label htmlFor="meeting-note-text" className="mb-1 block text-xs uppercase tracking-wide text-foreground-muted">
              {t('assistant.notes.textLabel')}
            </label>
            <textarea
              id="meeting-note-text"
              rows={8}
              maxLength={NOTE_TEXT_MAX}
              value={text}
              onChange={(e) => setText(e.target.value)}
              className={cn(inputClass, 'resize-y py-2')}
            />
            <div className="mt-1 flex items-center justify-between gap-2 text-[11px] text-foreground-muted">
              <span>{trimmed ? t('assistant.meetingNote.trimmed') : ''}</span>
              <span className="tabular-nums">
                {text.length}/{NOTE_TEXT_MAX}
              </span>
            </div>
          </div>
          <div>
            <label htmlFor="meeting-note-date" className="mb-1 block text-xs uppercase tracking-wide text-foreground-muted">
              {t('assistant.meetingNote.dateLabel')}
            </label>
            <input
              id="meeting-note-date"
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className={cn(inputClass, 'h-10 sm:w-56')}
            />
          </div>
          {error ? <Alert variant="danger">{error}</Alert> : null}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={onClose} disabled={saving}>
              {t('common.actions.cancel')}
            </Button>
            <Button variant="primary" size="sm" loading={saving} disabled={!text.trim()} onClick={() => void save()}>
              {t('assistant.notes.save')}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
