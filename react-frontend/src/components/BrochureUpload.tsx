import { useEffect, useRef, useState } from 'react';
import { Download, FileUp, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  friendlyApiError,
  ingestBrochure,
  listSampleBrochures,
  loadDataFile,
  uploadDocument,
} from '../lib/api';
import { Alert, Badge, Button, Spinner } from '../ui';
import type { IngestedBrochure, SampleBrochure } from '../types';

/** The model reads PDFs up to 4.5 MB. */
const MAX_BROCHURE_BYTES = 4_500_000;

interface BrochureUploadProps {
  /** Catalog product type the comparison is for, e.g. "Term Life Insurance". */
  productType: string;
  brochure: IngestedBrochure | null;
  onChange: (brochure: IngestedBrochure | null) => void;
  /** Four list products are already ticked, so there is no room for a brochure. */
  slotsFull: boolean;
}

/**
 * Optional extra for the Comparator: add one competitor brochure (PDF) to a
 * comparison. The file is uploaded, read once into a fixed structure, and
 * used only for this comparison. Sample brochures from a fictitious carrier
 * can be downloaded to try the flow.
 */
export function BrochureUpload({ productType, brochure, onChange, slotsFull }: BrochureUploadProps) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);
  const [phase, setPhase] = useState<'idle' | 'uploading' | 'reading'>('idle');
  const [error, setError] = useState<string | null>(null);
  const [samples, setSamples] = useState<SampleBrochure[]>([]);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listSampleBrochures()
      .then((list) => {
        if (!cancelled) setSamples(list);
      })
      .catch(() => {
        if (!cancelled) setSamples([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // A different product type is a different comparison.
  useEffect(() => {
    setError(null);
  }, [productType]);

  // One Corvane sample exists per product type (tools/brochures/build.py checks it).
  const sample = samples.find((s) => s.product_type === productType) ?? null;
  const busy = phase !== 'idle';

  const handleFile = async (file: File | undefined) => {
    if (!file) return;
    setError(null);
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setError(t('assistant.pages.comparator.brochure.pdfOnly'));
      return;
    }
    if (file.size > MAX_BROCHURE_BYTES) {
      setError(t('assistant.pages.comparator.brochure.tooLarge'));
      return;
    }
    try {
      setPhase('uploading');
      const uploaded = await uploadDocument(file, null);
      setPhase('reading');
      const result = await ingestBrochure(uploaded.document_id, productType);
      onChange(result);
    } catch (err) {
      setError(friendlyApiError(err));
    } finally {
      setPhase('idle');
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  const downloadSample = async () => {
    if (!sample) return;
    setDownloading(true);
    setError(null);
    try {
      const doc = await loadDataFile('samples', sample.key);
      if (!doc.download_url) throw new Error('No download link');
      const a = document.createElement('a');
      a.href = doc.download_url;
      a.rel = 'noopener';
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (err) {
      setError(friendlyApiError(err));
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="rounded-lg border border-dashed border-border-strong bg-background-muted/30 p-4">
      <div className="flex items-start gap-2.5">
        <FileUp className="mt-0.5 h-4 w-4 shrink-0 text-brand-2" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold">{t('assistant.pages.comparator.brochure.title')}</div>
          <p className="mt-0.5 text-xs text-foreground-muted">
            {t('assistant.pages.comparator.brochure.help')}
          </p>

          {brochure ? (
            <div className="mt-3 rounded-md border border-brand-2/40 bg-brand-2/5 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5 text-sm">
                    <span className="font-medium">{brochure.product.carrier_name}</span>
                    <span className="text-foreground-muted">— {brochure.product.product_name}</span>
                    <Badge variant="info">{t('assistant.pages.comparator.brochure.badge')}</Badge>
                  </div>
                  {brochure.product.tagline ? (
                    <p className="mt-1 text-xs text-foreground-muted">{brochure.product.tagline}</p>
                  ) : null}
                </div>
                <Button variant="ghost" size="sm" onClick={() => onChange(null)}>
                  <X className="h-3.5 w-3.5" />
                  {t('assistant.pages.comparator.brochure.remove')}
                </Button>
              </div>
              {brochure.warnings.length > 0 ? (
                <details className="mt-2 text-xs text-foreground-muted">
                  <summary className="cursor-pointer">
                    {t('assistant.pages.comparator.brochure.notes', { count: brochure.warnings.length })}
                  </summary>
                  <ul className="mt-1 list-disc space-y-0.5 pl-4">
                    {brochure.warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                </details>
              ) : null}
            </div>
          ) : (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <input
                ref={inputRef}
                type="file"
                accept="application/pdf,.pdf"
                className="sr-only"
                id="brochure-file"
                disabled={busy || slotsFull}
                onChange={(e) => void handleFile(e.target.files?.[0])}
              />
              <Button
                variant="secondary"
                size="sm"
                disabled={busy || slotsFull}
                onClick={() => inputRef.current?.click()}
              >
                {busy ? <Spinner size="sm" /> : <FileUp className="h-3.5 w-3.5" />}
                {phase === 'uploading'
                  ? t('assistant.pages.comparator.brochure.uploading')
                  : phase === 'reading'
                    ? t('assistant.pages.comparator.brochure.reading')
                    : t('assistant.pages.comparator.brochure.choose')}
              </Button>
              {slotsFull ? (
                <span className="text-xs text-foreground-muted">
                  {t('assistant.pages.comparator.brochure.noRoom')}
                </span>
              ) : null}
            </div>
          )}

          {sample && !brochure ? (
            <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
              <span className="text-xs text-foreground-muted">
                {t('assistant.pages.comparator.brochure.sampleLabel')}
              </span>
              <Button variant="secondary" size="sm" disabled={downloading} onClick={() => void downloadSample()}>
                {downloading ? <Spinner size="sm" /> : <Download className="h-3.5 w-3.5" />}
                {t('assistant.pages.comparator.brochure.sampleButton', { name: sample.product_name })}
              </Button>
            </div>
          ) : null}
          {error ? (
            <div className="mt-3">
              <Alert variant="danger">{error}</Alert>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
