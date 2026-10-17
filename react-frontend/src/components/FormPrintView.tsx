import { forwardRef } from 'react';
import { useTranslation } from 'react-i18next';
import { PDF_CONTENT_WIDTH_PX, PDF_TEXT_STYLE } from '../lib/pdf';
import type { FormFieldDef, FormFieldState, FormSchema } from '../types';

interface FormPrintViewProps {
  schema: FormSchema;
  fields: Record<string, FormFieldState>;
  advisorEmail: string | null;
  customerName?: string;
}

/*
 * The printable application form. It is built for paper: plain inline styles
 * and hex colours only, so the PDF looks the same in every theme and language.
 * It is mounted off-screen just long enough to be rendered into the PDF, then
 * removed (see FormPanel).
 *
 * Every field is listed. A field with no answer shows a blank line the
 * customer can write on. Each `data-pdf-block` is kept whole on a page.
 */

const INK = '#1f2937';
const MUTED = '#6b7280';
const RULE = '#9ca3af';
const BRAND = '#7c3aed';

/** Text to show for one field's current value. Empty string means "leave blank". */
function displayValue(
  field: FormFieldDef,
  raw: string | undefined,
  yes: string,
  no: string
): string {
  if (raw === undefined || raw === '') return '';
  if (field.type === 'boolean') return raw === 'true' ? yes : raw === 'false' ? no : '';
  if (field.type === 'select') {
    return field.options?.find((o) => o.value === raw)?.label ?? '';
  }
  if (field.type === 'currency') {
    const n = Number(raw);
    return Number.isFinite(n) ? `$${n.toLocaleString('en-US')}` : raw;
  }
  return raw;
}

export const FormPrintView = forwardRef<HTMLDivElement, FormPrintViewProps>(
  function FormPrintView({ schema, fields, advisorEmail, customerName }, ref) {
    const { t, i18n } = useTranslation();
    const prepared = new Date().toLocaleDateString(i18n.language, {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });
    const yes = t('assistant.form.yes');
    const no = t('assistant.form.no');

    return (
      <div
        ref={ref}
        aria-hidden
        style={{
          position: 'fixed',
          left: -10000,
          top: 0,
          width: PDF_CONTENT_WIDTH_PX - 48,
          background: '#ffffff',
          color: INK,
          ...PDF_TEXT_STYLE,
          fontSize: 13,
          lineHeight: 1.4,
        }}
      >
        <div data-pdf-block style={{ padding: '0 0 10px', background: '#ffffff' }}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'flex-end',
              borderBottom: `3px solid ${BRAND}`,
              paddingBottom: 10,
            }}
          >
            <div style={{ flex: '1 1 auto', minWidth: 0 }}>
              <div style={{ fontSize: 12, letterSpacing: 2, color: BRAND, fontWeight: 700 }}>
                AWESOME INSURANCE
              </div>
              <div style={{ fontSize: 22, fontWeight: 700, marginTop: 2 }}>{schema.title}</div>
              <div style={{ fontSize: 12, color: MUTED }}>{schema.product_name}</div>
            </div>
            <div style={{ flex: '0 0 auto', marginLeft: 16, textAlign: 'right', fontSize: 11, color: MUTED, whiteSpace: 'nowrap' }}>
              {customerName ? <div>{customerName}</div> : null}
              <div>{t('assistant.form.pdf.prepared', { date: prepared })}</div>
              {advisorEmail ? (
                <div>{t('assistant.form.pdf.advisor', { name: advisorEmail })}</div>
              ) : null}
            </div>
          </div>
        </div>

        {schema.sections.map((section) => (
          <div
            key={section.id}
            data-pdf-block
            style={{ padding: '8px 0 6px', background: '#ffffff' }}
          >
            <div
              style={{
                fontSize: 12,
                fontWeight: 700,
                textTransform: 'uppercase',
                letterSpacing: 1,
                color: BRAND,
                marginBottom: 6,
              }}
            >
              {section.title}
            </div>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr 1fr',
                columnGap: 24,
                rowGap: 10,
              }}
            >
              {section.fields.map((field) => {
                const value = displayValue(field, fields[field.path]?.value, yes, no);
                return (
                  <div key={field.path}>
                    <div style={{ fontSize: 10.5, color: MUTED }}>
                      {field.label}
                      {field.required ? ' *' : ''}
                    </div>
                    <div
                      style={{
                        minHeight: 20,
                        borderBottom: `1px solid ${RULE}`,
                        paddingTop: 2,
                        fontWeight: 600,
                        wordBreak: 'break-word',
                      }}
                    >
                      {value || '\u00a0'}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}

        <div data-pdf-block style={{ padding: '14px 0 0', background: '#ffffff' }}>
          <div style={{ fontSize: 10.5, color: MUTED, marginBottom: 18 }}>
            {t('assistant.form.pdf.requiredNote')}
          </div>
          {(
            [
              ['assistant.form.pdf.signatureApplicant', 'assistant.form.pdf.date'],
              ['assistant.form.pdf.signatureAdvisor', 'assistant.form.pdf.date'],
            ] as const
          ).map(([sigKey, dateKey]) => (
            <div
              key={sigKey}
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr 1fr',
                columnGap: 24,
                marginBottom: 30,
              }}
            >
              <div>
                <div style={{ borderBottom: `1px solid ${INK}`, height: 34 }} />
                <div style={{ fontSize: 10.5, color: MUTED, marginTop: 3 }}>{t(sigKey)}</div>
              </div>
              <div>
                <div style={{ borderBottom: `1px solid ${INK}`, height: 34 }} />
                <div style={{ fontSize: 10.5, color: MUTED, marginTop: 3 }}>{t(dateKey)}</div>
              </div>
            </div>
          ))}
          <div style={{ fontSize: 10, color: MUTED, marginTop: 22, borderTop: `1px solid ${RULE}`, paddingTop: 8 }}>
            {t('assistant.form.pdf.disclaimer')}
          </div>
        </div>
      </div>
    );
  }
);
