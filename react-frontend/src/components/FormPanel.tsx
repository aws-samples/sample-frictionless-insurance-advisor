import { useEffect, useMemo, useRef, useState } from 'react';

import { Check, Download, FileText, Pencil, PenLine, Sparkles, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { cn } from '../lib/cn';
import { exportBlocksToPdf, slugForFile } from '../lib/pdf';
import { friendlyApiError, submitApplication } from '../lib/api';
import type { ApplicationFieldInput, FormFieldDef, FormFieldState, FormSchema } from '../types';
import { Badge, Button, Card, Select } from '../ui';
import { useAdvisorEmail } from '../hooks/useAdvisor';
import { valuesOf, type FormFillProgress } from '../hooks/formSessions';
import { demoFill } from '../lib/demoData';
import { isRequired, isVisible, sensitivity } from '../lib/formRules';

import { ConfidenceIndicator } from './ConfidenceIndicator';
import { FormPrintView } from './FormPrintView';

interface FormPanelProps {
  schema: FormSchema;
  fields: Record<string, FormFieldState>;
  progress: FormFillProgress;
  /** Paths whose answers were kept when the product changed. */
  carriedOver: Set<string>;
  onFieldChange: (path: string, value: string) => void;
  onConfirmField: (path: string) => void;
  onClose: () => void;
  /** Shown on the downloaded PDF. */
  customerName?: string;
  /** The customer this application is for; null for a prospect with no record yet. */
  customerId?: string | null;
  /** Reference of the last save of this application; saving again overwrites that save. */
  savedRef?: string | null;
  /** Called with the reference a save returned, so it is remembered across close and reopen. */
  onSaved?: (productType: string, ref: string) => void;
}

/** Maps a schema field type onto the right input element. */
function FieldInput({
  field,
  value,
  onChange,
  disabled = false,
}: {
  field: FormFieldDef;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const secret = sensitivity(field) === 'secret';
  const yes = t('assistant.form.yes');
  const no = t('assistant.form.no');
  const inputClass =
    'h-10 w-full rounded-md border border-border bg-background-elevated px-3 text-sm text-foreground ' +
    'transition-[border-color,box-shadow] duration-150 placeholder:text-foreground-muted ' +
    'focus-visible:outline-none focus-visible:border-ring/60 focus-visible:ring-2 focus-visible:ring-ring/30';

  if (field.type === 'select') {
    return (
      <Select
        id={field.path}
        value={value}
        disabled={disabled}
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
      <Select id={field.path} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        <option value="">—</option>
        <option value="true">{yes}</option>
        <option value="false">{no}</option>
      </Select>
    );
  }

  return (
    <input
      id={field.path}
      className={inputClass}
      // A secret field (the SSN) is masked and kept out of the browser's autofill.
      type={secret ? 'password' : field.type === 'date' ? 'date' : field.type === 'text' ? 'text' : 'number'}
      autoComplete={secret ? 'off' : undefined}
      inputMode={field.type === 'currency' || field.type === 'number' ? 'decimal' : undefined}
      value={value}
      placeholder={field.hint}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

/**
 * The application form, rendered from a schema and filled as the conversation
 * progresses. Occupies the left panel of the Text and Voice pages so the advisor can
 * watch fields land without losing sight of the conversation on the right.
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
  customerName,
  customerId = null,
  savedRef = null,
  onSaved,
}: FormPanelProps) {
  const { t } = useTranslation();
  // The current answers, for the form rules (what is shown, what is required).
  const values = useMemo(() => valuesOf(fields), [fields]);
  // "Send for Signature" saves the whole form (POST /applications). `saved` remembers the
  // reference and the answers it was saved with; the form counts as sent only while the
  // answers still match, so any later change (typed, or filled by an assistant) reopens it.
  const [saved, setSaved] = useState<{ ref: string; snapshot: string; omitted: string[]; replaced: boolean; revision: number } | null>(null);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const snapshot = useMemo(
    () => JSON.stringify(Object.entries(fields).map(([p, f]) => [p, f.value]).sort()),
    [fields]
  );
  const sent = saved !== null && saved.snapshot === snapshot;
  // PDF download: the printable view is mounted only while exporting.
  const advisorEmail = useAdvisorEmail();
  const printRef = useRef<HTMLDivElement>(null);
  const [exporting, setExporting] = useState(false);
  const [pdfError, setPdfError] = useState<string | null>(null);

  // A different product means a different application, so a previous "sent"
  // must not carry over — the new form has its own fields to complete.
  useEffect(() => {
    setSaved(null);
    setSendError(null);
  }, [schema.form_id]);

  const readyToSend =
    progress.requiredTotal > 0 && progress.requiredFilled === progress.requiredTotal;

  useEffect(() => {
    if (!exporting) return;
    let cancelled = false;
    (async () => {
      try {
        if (!printRef.current) throw new Error('Print view not ready');
        await exportBlocksToPdf(printRef.current, {
          filename: `${slugForFile(schema.title)}${customerName ? `-${slugForFile(customerName)}` : ''}.pdf`,
        });
      } catch (err) {
        if (!cancelled) setPdfError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setExporting(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [exporting, schema.title, customerName]);

  // Editing a required field back to empty after sending should also undo the
  // sent state; the application is no longer complete.
  useEffect(() => {
    if (!readyToSend) setSaved(null);
  }, [readyToSend]);
  const handleSend = async () => {
    setSending(true);
    setSendError(null);
    const taken = snapshot;
    try {
      const payload: Record<string, ApplicationFieldInput> = {};
      for (const section of schema.sections) {
        for (const f of section.fields) {
          const st = fields[f.path];
          // A secret field (the Social Security number) is typed on screen and never sent.
          if (!st || st.value === '' || sensitivity(f) === 'secret') continue;
          payload[f.path] = { value: st.value, origin: st.origin, visible: isVisible(f, values) };
        }
      }
      const res = await submitApplication(schema.product_type, customerId, payload, savedRef);
      onSaved?.(schema.product_type, res.ref);
      setSaved({
        ref: res.ref,
        snapshot: taken,
        omitted: res.omitted,
        replaced: res.replaced === true,
        revision: res.revision ?? 1,
      });
    } catch (err) {
      setSendError(friendlyApiError(err));
    } finally {
      setSending(false);
    }
  };
  // "Fill demo data": sample answers for the empty fields only; anything already there is kept.
  const [demoCount, setDemoCount] = useState<number | null>(null);
  const handleDemoFill = () => {
    const added = demoFill(schema, values);
    for (const [path, value] of Object.entries(added)) onFieldChange(path, value);
    setDemoCount(Object.keys(added).length);
  };
  useEffect(() => {
    setDemoCount(null);
  }, [schema.form_id]);
  const omittedLabels = (saved?.omitted ?? [])
    .map((p) => schema.sections.flatMap((s) => s.fields).find((f) => f.path === p)?.label)
    .filter((l): l is string => Boolean(l));

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
          <div className="flex shrink-0 items-center gap-2">
            {/* Demo only: the same sample answers for every customer, in the empty fields. */}
            <Button
              variant="secondary"
              size="sm"
              onClick={handleDemoFill}
              disabled={sent}
              title={t('assistant.form.fillDemoHint')}
            >
              <Sparkles className="h-3.5 w-3.5" />
              {t('assistant.form.fillDemo')}
            </Button>
            {/* A clear outline so the way out of the form is easy to see. */}
            <Button
              variant="secondary"
              size="sm"
              onClick={onClose}
              className="font-semibold text-foreground"
            >
              <X className="h-3.5 w-3.5" />
              {t('common.actions.close')}
            </Button>
          </div>
        </div>

        {demoCount !== null ? (
          <p role="status" className="mt-3 text-xs text-foreground-muted">
            {demoCount > 0 ? t('assistant.form.demoFilled', { count: demoCount }) : t('assistant.form.demoNothing')}
          </p>
        ) : null}
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
      {schema.sections.map((section) => {
        // Only fields whose condition holds right now; a section with none is left out.
        const shown = section.fields.filter((f) => isVisible(f, values));
        if (shown.length === 0) return null;
        return (
        <section key={section.id}>
          <header className="mb-3 flex items-center gap-2">
            <h3 className="text-sm font-semibold uppercase tracking-wide">{section.title}</h3>
          </header>
          <Card className="p-4 sm:p-5">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {shown.map((field) => {
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
                      {isRequired(field, values) ? (
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
                        disabled={sent}
                        onChange={(v) => onFieldChange(field.path, v)}
                      />
                    </div>

                    {sensitivity(field) === 'secret' ? (
                      <p className="mt-1.5 text-xs text-foreground-muted">{t('assistant.form.secretNote')}</p>
                    ) : null}
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
        );
      })}

      {/* Missing required fields */}
      {progress.missingLabels.length > 0 ? (
        <Card className="p-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-foreground-muted">
            {t('assistant.form.stillNeeded')}
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {progress.missingLabels.map((label, i) => (
              <Badge key={`${label}-${i}`} variant="neutral">
                {label}
              </Badge>
            ))}
          </div>
        </Card>
      ) : null}

      {/* Send for signature saves the application. Unlocks once every required field holds
          a value; the label explains why it is disabled rather than leaving the advisor
          guessing. After a save the fields are locked until "Edit again". */}
      <div className="flex flex-col items-stretch gap-2 pb-2">
        <div className="flex flex-col gap-2 sm:flex-row">
          <Button
            variant="primary"
            size="lg"
            className="sm:flex-1"
            disabled={!readyToSend || sent || sending}
            onClick={() => void handleSend()}
            title={
              readyToSend
                ? undefined
                : t('assistant.form.sendBlocked', { count: progress.missingLabels.length })
            }
          >
            {sent ? (
              <>
                <Check className="h-4 w-4" />
                {t('assistant.form.saved')}
              </>
            ) : sending ? (
              t('assistant.form.saving')
            ) : (
              <>
                <PenLine className="h-4 w-4" />
                {t('assistant.form.sendForSignature')}
              </>
            )}
          </Button>
          {/* Download PDF works at any time, complete or not; it sits next to
              Send for signature so the two end-of-form actions are together. */}
          <Button
            variant="secondary"
            size="lg"
            disabled={exporting}
            onClick={() => {
              setPdfError(null);
              setExporting(true);
            }}
          >
            <Download className="h-4 w-4" />
            {exporting ? t('assistant.form.pdf.preparing') : t('assistant.form.downloadPdf')}
          </Button>
          {sent ? (
            <Button variant="secondary" size="lg" onClick={() => setSaved(null)}>
              <Pencil className="h-4 w-4" />
              {t('assistant.form.editAgain')}
            </Button>
          ) : null}
        </div>
        {sendError ? (
          <p className="text-center text-xs text-danger" role="alert">
            {t('assistant.form.saveFailed', { message: sendError })}
          </p>
        ) : null}
        {pdfError ? (
          <p className="text-center text-xs text-danger" role="alert">
            {t('assistant.form.pdf.failed', { message: pdfError })}
          </p>
        ) : null}
        <p className="text-center text-xs text-foreground-muted">
          {sent
            ? saved?.replaced
              ? t('assistant.form.savedHintReplaced', { ref: saved.ref, revision: saved.revision })
              : t('assistant.form.savedHint', { ref: saved?.ref ?? '' })
            : readyToSend
              ? t('assistant.form.sendReadyHint')
              : t('assistant.form.sendBlocked', { count: progress.missingLabels.length })}
        </p>
        {sent && omittedLabels.length > 0 ? (
          <p className="text-center text-xs text-foreground-muted">
            {t('assistant.form.savedOmitted', { fields: omittedLabels.join(', ') })}
          </p>
        ) : null}
      </div>
      {exporting ? (
        <FormPrintView
          ref={printRef}
          schema={schema}
          fields={fields}
          advisorEmail={advisorEmail}
          customerName={customerName}
        />
      ) : null}
    </div>
  );
}
