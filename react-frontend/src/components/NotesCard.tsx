import { FormEvent, useEffect, useMemo, useState } from 'react';

import { Bot, Plus, StickyNote, Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { addNote, deleteNote } from '../lib/api';
import { cn } from '../lib/cn';
import { Alert, Badge, Button, Card, IconButton, Select } from '../ui';
import type { CustomerNote, CustomerNoteType } from '../types';

interface NotesCardProps {
  customerId: string;
  notes: CustomerNote[];
}

// Mirrors NOTE_TEXT_MAX in lambda/profile; the server enforces it.
const NOTE_TEXT_MAX = 1000;

/**
 * Format a timestamp or a date-only string. A bare YYYY-MM-DD is parsed as a
 * local calendar date: `new Date('2027-03-15')` is UTC midnight, which shows
 * as the previous day west of Greenwich.
 */
function formatWhen(value: string | undefined, locale: string): string {
  if (!value) return '';
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  const parsed = dateOnly
    ? new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]))
    : new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium' }).format(parsed);
}

/**
 * Life-event and interaction notes for one customer.
 *
 * The list is kept in local state so an add or delete shows immediately; it
 * resyncs from props whenever the parent's copy changes (sidebar refresh,
 * including notes the assistant added). Deleting is UI-only by design: the
 * assistant can add notes but has no tool to remove them.
 */
export function NotesCard({ customerId, notes }: NotesCardProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;

  // `notes` must be referentially stable (the parent passes a module-level
  // empty array when a customer has none); a fresh [] each render would
  // re-run this effect forever.
  const [items, setItems] = useState<CustomerNote[]>(notes);
  useEffect(() => {
    setItems(notes);
  }, [notes]);

  const [adding, setAdding] = useState(false);
  const [noteType, setNoteType] = useState<CustomerNoteType>('life_event');
  const [text, setText] = useState('');
  const [eventDate, setEventDate] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // A different customer means a different notebook: drop any draft.
  useEffect(() => {
    setAdding(false);
    setText('');
    setEventDate('');
    setError(null);
    setConfirmingId(null);
  }, [customerId]);

  const sorted = useMemo(
    () => [...items].sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [items]
  );

  const resetForm = () => {
    setAdding(false);
    setText('');
    setEventDate('');
    setNoteType('life_event');
  };

  const handleAdd = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || saving) return;
    setSaving(true);
    setError(null);
    try {
      const saved = await addNote(customerId, {
        type: noteType,
        text: trimmed,
        ...(eventDate ? { event_date: eventDate } : {}),
      });
      setItems((prev) => [...prev, saved]);
      resetForm();
    } catch (err) {
      setError(t('assistant.notes.saveFailed', { message: err instanceof Error ? err.message : String(err) }));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (noteId: string) => {
    setDeletingId(noteId);
    setError(null);
    try {
      await deleteNote(customerId, noteId);
      setItems((prev) => prev.filter((n) => n.note_id !== noteId));
    } catch (err) {
      setError(t('assistant.notes.deleteFailed', { message: err instanceof Error ? err.message : String(err) }));
    } finally {
      setDeletingId(null);
      setConfirmingId(null);
    }
  };

  const inputClass =
    'w-full rounded-md border border-border bg-background-elevated px-3 text-sm text-foreground ' +
    'placeholder:text-foreground-muted/70 focus-visible:outline-none focus-visible:border-ring/60 ' +
    'focus-visible:ring-2 focus-visible:ring-ring/30';

  return (
    <section>
      <header className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <StickyNote className="h-4 w-4 text-brand-2" />
          <h3 className="text-sm font-semibold uppercase tracking-wide">
            {t('assistant.notes.heading')}
          </h3>
          {sorted.length > 0 ? <Badge variant="neutral">{sorted.length}</Badge> : null}
        </div>
        {!adding ? (
          <Button variant="ghost" size="sm" onClick={() => setAdding(true)}>
            <Plus className="h-3.5 w-3.5" />
            {t('assistant.notes.add')}
          </Button>
        ) : null}
      </header>

      <Card className="p-4">
        {error ? (
          <div className="mb-3">
            <Alert variant="danger">{error}</Alert>
          </div>
        ) : null}

        {adding ? (
          <form onSubmit={handleAdd} className="mb-4 space-y-3 border-b border-border pb-4">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <label
                  htmlFor="note-type"
                  className="mb-1 block text-xs uppercase tracking-wide text-foreground-muted"
                >
                  {t('assistant.notes.typeLabel')}
                </label>
                <Select
                  id="note-type"
                  value={noteType}
                  onChange={(e) => setNoteType(e.target.value as CustomerNoteType)}
                >
                  <option value="life_event">{t('assistant.notes.type.life_event')}</option>
                  <option value="interaction">{t('assistant.notes.type.interaction')}</option>
                </Select>
              </div>
              <div>
                <label
                  htmlFor="note-date"
                  className="mb-1 block text-xs uppercase tracking-wide text-foreground-muted"
                >
                  {t('assistant.notes.dateLabel')}
                </label>
                <input
                  id="note-date"
                  type="date"
                  value={eventDate}
                  onChange={(e) => setEventDate(e.target.value)}
                  className={cn(inputClass, 'h-10')}
                />
              </div>
            </div>
            <div>
              <label
                htmlFor="note-text"
                className="mb-1 block text-xs uppercase tracking-wide text-foreground-muted"
              >
                {t('assistant.notes.textLabel')}
              </label>
              <textarea
                id="note-text"
                rows={3}
                maxLength={NOTE_TEXT_MAX}
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={t('assistant.notes.textPlaceholder')}
                className={cn(inputClass, 'resize-y py-2')}
              />
              <div className="mt-1 text-right text-[11px] tabular-nums text-foreground-muted">
                {text.length}/{NOTE_TEXT_MAX}
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" size="sm" onClick={resetForm} disabled={saving}>
                {t('common.actions.cancel')}
              </Button>
              <Button type="submit" variant="primary" size="sm" loading={saving} disabled={!text.trim()}>
                {t('assistant.notes.save')}
              </Button>
            </div>
          </form>
        ) : null}

        {sorted.length === 0 ? (
          <p className="text-sm italic text-foreground-muted">{t('assistant.notes.empty')}</p>
        ) : (
          <ul className="space-y-3">
            {sorted.map((note) => (
              <li key={note.note_id} className="group flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge variant={note.type === 'life_event' ? 'brand' : 'neutral'}>
                      {t(`assistant.notes.type.${note.type}`, { defaultValue: note.type })}
                    </Badge>
                    {note.event_date ? (
                      <span className="text-xs text-foreground-muted">
                        {t('assistant.notes.eventOn', { date: formatWhen(note.event_date, locale) })}
                      </span>
                    ) : null}
                    <span className="text-xs text-foreground-muted">
                      · {t('assistant.notes.addedOn', { date: formatWhen(note.created_at, locale) })}
                    </span>
                    {note.source === 'assistant' ? (
                      <span
                        className="inline-flex items-center gap-1 text-xs text-foreground-muted"
                        title={t('assistant.notes.byAssistantHint')}
                      >
                        <Bot className="h-3 w-3" />
                        {t('assistant.notes.byAssistant')}
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-1 whitespace-pre-wrap break-words text-sm text-foreground">
                    {note.text}
                  </p>
                </div>

                {confirmingId === note.note_id ? (
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      variant="destructive"
                      size="sm"
                      loading={deletingId === note.note_id}
                      onClick={() => void handleDelete(note.note_id)}
                    >
                      {t('assistant.notes.confirmDelete')}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setConfirmingId(null)}
                      disabled={deletingId === note.note_id}
                    >
                      {t('common.actions.cancel')}
                    </Button>
                  </div>
                ) : (
                  <IconButton
                    aria-label={t('assistant.notes.delete')}
                    variant="ghost"
                    size="sm"
                    onClick={() => setConfirmingId(note.note_id)}
                    className="shrink-0 opacity-60 transition-opacity group-hover:opacity-100"
                  >
                    <Trash2 className="h-4 w-4" />
                  </IconButton>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </section>
  );
}
