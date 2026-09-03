import { useEffect, useState } from 'react';

import { ArrowLeft, Check, FileText, PenLine } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { cn } from '../lib/cn';
import type { FormFieldDef, FormFieldState, FormSchema } from '../types';
import { Badge, Button, Card, Select } from '../ui';
import type { FormFillProgress } from '../hooks/useFormFill';

import { ConfidenceIndicator } from './ConfidenceIndicator';

interface FormPanelProps {
  schema: FormSchema;
  fields: Record<string, FormFieldState>;
  progress: FormFillProgress;
  /** Paths whose answers were kept when the product changed. */
  carriedOver: Set<string>;
  onFieldChange: (path: string, value: string) => void;
  onConfirmField: (path: string) => void;
  onClose: () => void;
}

/** Maps a schema field type onto the right input element. */
function FieldInput({
  field,
  value,
  onChange,
}: {
  field: FormFieldDef;
  value: string;
  onChange: (value: string) => void;
}) {
  const inputClass =
    'h-10 w-full rounded-md border border-border bg-background-elevated px-3 text-sm text-foreground ' +
    'transition-[border-color,box-shadow] duration-150 placeholder:text-foreground-muted ' +
    'focus-visible:outline-none focus-visible:border-ring/60 focus-visible:ring-2 focus-visible:ring-ring/30';

  if (field.type === 'select') {
    return (
      <Select
        id={field.path}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">—</option>
        {field.options?.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </Select>
    );
  }

  if (field.type === 'boolean') {
    return (
      <Select id={field.path} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">—</option>
        <option value="true">Yes</option>
        <option value="false">No</option>
      </Select>
    );
  }

  return (
    <input
      id={field.path}
      className={inputClass}
      type={field.type === 'date' ? 'date' : field.type === 'text' ? 'text' : 'number'}
      inputMode={field.type === 'currency' || field.type === 'number' ? 'decimal' : undefined}
      value={value}
      placeholder={field.hint}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

/**
 * The application form, rendered from a schema and filled as the conversation
 * progresses. Occupies the left panel of the Voice page so the advisor can
 * watch fields land without losing sight of the transcript on the right.
 *
 * Every field stays editable — auto-fill is a starting point, not a lock.
 */
export function FormPanel({
  schema,
  fields,
  progress,
  carriedOver,
  onFieldChange,
  onConfirmField,
  onClose,
}: FormPanelProps) {
  const { t } = useTranslation();
  // Mock action — no backend. Kept local so the button can show it fired.
  const [sent, setSent] = useState(false);

  // A different product means a different application, so a previous "sent"
  // must not carry over — the new form has its own fields to complete.
  useEffect(() => {
    setSent(false);
  }, [schema.form_id]);

  const readyToSend =
    progress.requiredTotal > 0 && progress.requiredFilled === progress.requiredTotal;

  // Editing a required field back to empty after sending should also undo the
  // sent state; the application is no longer complete.
  useEffect(() => {
    if (!readyToSend) setSent(false);
  }, [readyToSend]);

  return (
    <div className="space-y-6 p-6 sm:p-8">
      {/* Header + progress */}
      <Card feature className="p-5 sm:p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3 min-w-0">
            <FileText className="mt-0.5 h-5 w-5 shrink-0 text-brand-2" />
            <div className="min-w-0">
              <h2 className="truncate text-lg font-bold tracking-tight">{schema.title}</h2>
              <p className="mt-0.5 truncate text-xs text-foreground-muted">
                {schema.product_name}
              </p>
            </div>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={onClose}
            className="shrink-0"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            {t('assistant.form.backToProfile')}
          </Button>
        </div>

        <div className="mt-5">
          <div className="flex items-center justify-between gap-3 text-xs">
            <span className="text-foreground-muted">
              {t('assistant.form.progress', {
                filled: progress.requiredFilled,
                total: progress.requiredTotal,
              })}
            </span>
            <span className="font-semibold tabular-nums text-brand-gradient">
              {progress.percentage}%
            </span>
          </div>
          <div
            className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-background-muted"
            role="progressbar"
            aria-valuenow={progress.percentage}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={t('assistant.form.progressLabel')}
          >
            <div
              className="h-full rounded-full transition-[width] duration-500"
              style={{
                width: `${progress.percentage}%`,
                background:
                  'linear-gradient(90deg, rgb(var(--brand-1)), rgb(var(--brand-2)), rgb(var(--brand-3)))',
              }}
            />
          </div>
          {progress.pendingReview > 0 || carriedOver.size > 0 ? (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {progress.pendingReview > 0 ? (
                <Badge variant="warning" dot>
                  {t('assistant.form.pendingReview', { count: progress.pendingReview })}
                </Badge>
              ) : null}
              {carriedOver.size > 0 ? (
                <Badge variant="brand" dot>
                  {t('assistant.form.carriedOver', { count: carriedOver.size })}
                </Badge>
              ) : null}
            </div>
          ) : null}

          {/* What the dot beside each field means. Without this the colours
              are guesswork — the advisor has no way to know violet is profile
              data and amber wants a second look. */}
          <div className="mt-4 border-t border-border pt-3">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
              {(
                [
                  ['bg-brand-2', 'legendProfile'],
                  ['bg-success', 'legendHeard'],
                  ['bg-warning', 'legendConfirm'],
                  ['bg-foreground-muted', 'legendManual'],
                ] as const
              ).map(([dot, key]) => (
                <span key={key} className="inline-flex items-center gap-1.5">
                  <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', dot)} />
                  <span className="text-xs text-foreground-muted">
                    {t(`assistant.form.${key}`)}
                  </span>
                </span>
              ))}
            </div>
          </div>
        </div>
      </Card>

      {/* Sections */}
      {schema.sections.map((section) => (
        <section key={section.id}>
          <header className="mb-3 flex items-center gap-2">
            <h3 className="text-sm font-semibold uppercase tracking-wide">{section.title}</h3>
          </header>
          <Card className="p-4 sm:p-5">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {section.fields.map((field) => {
                const state = fields[field.path];
                const value = state?.value ?? '';
                // A choice field holding a value that isn't one of its options
                // would render as blank while the state looks answered. Treat
                // that as unresolved rather than showing a confident dot.
                const isUnrenderable =
                  value !== '' &&
                  ((field.type === 'select' &&
                    !(field.options ?? []).some((o) => o.value === value)) ||
                    (field.type === 'boolean' && value !== 'true' && value !== 'false'));
                const isReview = state?.tier === 'review' || isUnrenderable;

                return (
                  <div key={field.path} className="min-w-0">
                    <div className="mb-1.5 flex items-center gap-1.5">
                      <label
                        htmlFor={field.path}
                        className="text-xs uppercase tracking-wide text-foreground-muted"
                      >
                        {field.label}
                      </label>
                      {field.required ? (
                        <span className="text-danger" aria-hidden>
                          *
                        </span>
                      ) : null}
                      {state && !isUnrenderable ? (
                        <ConfidenceIndicator
                          tier={state.tier}
                          confidence={state.confidence}
                          origin={state.origin}
                          source={state.source}
                        />
                      ) : null}
                    </div>

                    <div
                      className={cn(
                        'rounded-md',
                        isReview && 'ring-1 ring-warning/40'
                      )}
                    >
                      <FieldInput
                        field={field}
                        value={value}
                        onChange={(v) => onFieldChange(field.path, v)}
                      />
                    </div>

                    {isReview ? (
                      <button
                        type="button"
                        onClick={() => onConfirmField(field.path)}
                        className="mt-1.5 inline-flex items-center gap-1 text-xs text-warning hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 rounded"
                      >
                        <Check className="h-3 w-3" />
                        {t('assistant.form.looksRight')}
                      </button>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </Card>
        </section>
      ))}

      {/* Missing required fields */}
      {progress.missingLabels.length > 0 ? (
        <Card className="p-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-foreground-muted">
            {t('assistant.form.stillNeeded')}
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {progress.missingLabels.map((label) => (
              <Badge key={label} variant="neutral">
                {label}
              </Badge>
            ))}
          </div>
        </Card>
      ) : null}

      {/* Send for signature — mock. Unlocks once every required field holds a
          value; the label explains why it is disabled rather than leaving the
          advisor guessing. */}
      <div className="flex flex-col items-stretch gap-2 pb-2">
        <Button
          variant="primary"
          size="lg"
          fullWidth
          disabled={!readyToSend || sent}
          onClick={() => setSent(true)}
          title={
            readyToSend
              ? undefined
              : t('assistant.form.sendBlocked', { count: progress.missingLabels.length })
          }
        >
          {sent ? (
            <>
              <Check className="h-4 w-4" />
              {t('assistant.form.sent')}
            </>
          ) : (
            <>
              <PenLine className="h-4 w-4" />
              {t('assistant.form.sendForSignature')}
            </>
          )}
        </Button>
        <p className="text-center text-xs text-foreground-muted">
          {sent
            ? t('assistant.form.sentHint')
            : readyToSend
              ? t('assistant.form.sendReadyHint')
              : t('assistant.form.sendBlocked', { count: progress.missingLabels.length })}
        </p>
      </div>
    </div>
  );
}
