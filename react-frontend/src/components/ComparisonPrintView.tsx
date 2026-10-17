import { forwardRef } from 'react';
import { useTranslation } from 'react-i18next';
import { fmtDate } from '../lib/format';
import { PDF_CONTENT_WIDTH_PX, PDF_TEXT_STYLE } from '../lib/pdf';
import { ratingDetail, summariseRatings } from '../lib/ratings';
import type { ComparisonResponse } from '../types';

interface ComparisonPrintViewProps {
  data: ComparisonResponse;
  advisorEmail: string | null;
}

/*
 * The comparison as a handout for the customer. Plain
 * inline styles and hex colours so it looks the same in every theme and
 * language. It is mounted off-screen only while the PDF is being made.
 *
 * It carries Awesome branding, the date, the advisor, the summary, every
 * table, the carrier ratings and a disclaimer. It deliberately has NO
 * "Internal use only" marking: that badge belongs to the advisor's screen.
 */

const INK = '#1f2937';
const MUTED = '#6b7280';
const RULE = '#d1d5db';
const BRAND = '#7c3aed';
const SOFT = '#f5f3ff';

const cell = (extra: React.CSSProperties = {}): React.CSSProperties => ({
  border: `1px solid ${RULE}`,
  padding: '6px 8px',
  verticalAlign: 'top',
  textAlign: 'left',
  fontSize: 11.5,
  wordBreak: 'break-word',
  ...extra,
});

function text(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(text).join(', ');
  return String(value);
}

export const ComparisonPrintView = forwardRef<HTMLDivElement, ComparisonPrintViewProps>(
  function ComparisonPrintView({ data, advisorEmail }, ref) {
    const { t, i18n } = useTranslation();
    const locale = i18n.resolvedLanguage ?? i18n.language;
    const today = new Date();
    const prepared = today.toLocaleDateString(locale, { year: 'numeric', month: 'long', day: 'numeric' });
    const products = Array.isArray(data.products) ? data.products : [];
    const sections = Array.isArray(data.sections) ? data.sections : [];
    const ratings = summariseRatings(products);
    const uploaded = products.filter((p) => p.uploaded);
    const dated = products.map((p) => p.last_updated).filter((d): d is string => Boolean(d)).sort();
    const sourceDate = dated.length > 0 ? fmtDate(dated[dated.length - 1], locale) : prepared;
    const outlookWord = t('assistant.pages.comparator.ratings.outlook');

    const headRow = (
      <tr>
        <th style={cell({ width: '26%', background: SOFT })} />
        {products.map((p) => (
          <th key={p.id} style={cell({ background: SOFT })}>
            <div style={{ fontWeight: 700 }}>{text(p.name)}</div>
            <div style={{ fontWeight: 400, color: MUTED, fontSize: 10.5 }}>{text(p.carrier)}</div>
          </th>
        ))}
      </tr>
    );

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
          fontSize: 12.5,
          lineHeight: 1.4,
        }}
      >
        <div data-pdf-block style={{ background: '#ffffff', paddingBottom: 8 }}>
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
              <div style={{ fontSize: 12, letterSpacing: 2, color: BRAND, fontWeight: 700 }}>AWESOME INSURANCE</div>
              <div style={{ fontSize: 21, fontWeight: 700, marginTop: 2 }}>{text(data.title)}</div>
              <div style={{ fontSize: 11.5, color: MUTED }}>{t('assistant.pages.comparator.pdf.heading')}</div>
            </div>
            <div style={{ flex: '0 0 auto', marginLeft: 16, textAlign: 'right', fontSize: 11, color: MUTED, whiteSpace: 'nowrap' }}>
              <div>{t('assistant.pages.comparator.pdf.prepared', { date: prepared })}</div>
              {advisorEmail ? <div>{t('assistant.pages.comparator.pdf.advisor', { name: advisorEmail })}</div> : null}
            </div>
          </div>
        </div>

        {data.summary ? (
          <div data-pdf-block style={{ background: '#ffffff', padding: '8px 0' }}>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 1, textTransform: 'uppercase', color: BRAND }}>
              {t('assistant.pages.comparator.resultSummary')}
            </div>
            <div style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>{text(data.summary)}</div>
          </div>
        ) : null}

        {ratings.hasAny ? (
          <div data-pdf-block style={{ background: '#ffffff', padding: '8px 0' }}>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 1, textTransform: 'uppercase', color: BRAND, marginBottom: 4 }}>
              {t('assistant.pages.comparator.ratings.title')}
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed' }}>
              <thead>{headRow}</thead>
              <tbody>
                {ratings.rows.map((row) => (
                  <tr key={row.agency}>
                    <td style={cell({ fontWeight: 700 })}>{row.agency}</td>
                    {row.cells.map((r, i) => (
                      <td key={`${row.agency}-${i}`} style={cell()}>
                        {r ? (
                          <>
                            <span style={{ fontWeight: 700 }}>{r.rating}</span>
                            {ratingDetail(r, outlookWord) ? (
                              <div style={{ color: MUTED, fontSize: 10.5 }}>{ratingDetail(r, outlookWord)}</div>
                            ) : null}
                          </>
                        ) : (
                          <span style={{ color: MUTED }}>{t('assistant.pages.comparator.ratings.notStated')}</span>
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ color: MUTED, fontSize: 10, marginTop: 4 }}>
              {ratings.anyIllustrative ? <div>{t('assistant.pages.comparator.ratings.illustrativeNote')}</div> : null}
              {ratings.anyFromBrochure ? <div>{t('assistant.pages.comparator.ratings.brochureNote')}</div> : null}
              {ratings.asOf ? <div>{t('assistant.pages.comparator.ratings.asOf', { date: fmtDate(ratings.asOf, locale) })}</div> : null}
            </div>
          </div>
        ) : null}

        {sections.map((section) => (
          <div key={section.title} data-pdf-block style={{ background: '#ffffff', padding: '8px 0' }}>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 1, textTransform: 'uppercase', color: BRAND, marginBottom: 4 }}>
              {text(section.title)}
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed' }}>
              <thead>{headRow}</thead>
              <tbody>
                {(Array.isArray(section.rows) ? section.rows : []).map((row) => (
                  <tr key={row.attribute}>
                    <td style={cell({ fontWeight: 700 })}>{text(row.attribute)}</td>
                    {(Array.isArray(row.values) ? row.values : []).map((v, i) => (
                      <td key={`${row.attribute}-${i}`} style={cell()}>
                        {text(v)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}

        <div data-pdf-block style={{ background: '#ffffff', padding: '10px 0 0', borderTop: `1px solid ${RULE}`, marginTop: 6 }}>
          {uploaded.map((p) => (
            <div key={p.id} style={{ color: MUTED, fontSize: 10.5, marginBottom: 3 }}>
              {t('assistant.pages.comparator.pdf.uploadedNote', { name: text(p.name) })}
            </div>
          ))}
          <div style={{ color: MUTED, fontSize: 10.5 }}>
            {t('assistant.pages.comparator.pdf.customerDisclaimer', { date: sourceDate })}
          </div>
          {data.disclaimer ? (
            <div style={{ color: MUTED, fontSize: 10.5, marginTop: 3, fontStyle: 'italic' }}>{text(data.disclaimer)}</div>
          ) : null}
        </div>
      </div>
    );
  }
);
