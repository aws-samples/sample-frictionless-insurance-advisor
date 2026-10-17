import { useEffect, useMemo, useRef, useState } from 'react';

import { AlertTriangle, Check, Download, Scale } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { useAdvisorEmail } from '../hooks/useAdvisor';
import {
  compareProducts,
  loadProductsByType,
  loadProductTypes,
} from '../lib/api';
import { exportBlocksToPdf } from '../lib/pdf';
import { PRODUCT_LINES, typesForLine, type ProductLine } from '../lib/productLines';
import { ratingDetail, summariseRatings } from '../lib/ratings';
import { cn } from '../lib/cn';
import { fmtDate } from '../lib/format';
import { Alert, Badge, Button, Card, Select, Spinner } from '../ui';
import type { CatalogProduct, ComparisonResponse, IngestedBrochure } from '../types';
import { BrochureUpload } from './BrochureUpload';
import { ComparisonPrintView } from './ComparisonPrintView';

const MIN_PRODUCTS = 2;
const MAX_PRODUCTS = 4;
// Keep in sync with STALE_AFTER_DAYS in lambda/comparator. Only
// used here for the staleness label copy; the stale/fresh decision is made
// server-side.
const STALE_AFTER_DAYS = 90;

export function ComparatorPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;

  // Catalog dimensions
  const [productTypes, setProductTypes] = useState<string[]>([]);
  const [typesLoading, setTypesLoading] = useState<boolean>(true);
  const [typesError, setTypesError] = useState<string | null>(null);

  // Current selection state. The advisor first picks a product line (Life / General /
  // Health); the type dropdown then lists only that line's types.
  const [line, setLine] = useState<ProductLine>('life');
  const [selectedType, setSelectedType] = useState<string>('');
  const [productsForType, setProductsForType] = useState<CatalogProduct[]>([]);
  const [productsLoading, setProductsLoading] = useState<boolean>(false);
  const [productsError, setProductsError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  // One competitor brochure the advisor uploaded for this comparison.
  // It counts toward the 2..4 products and is tied to the selected product type.
  const [brochure, setBrochure] = useState<IngestedBrochure | null>(null);
  // Comparison result state
  const [comparing, setComparing] = useState<boolean>(false);
  const [comparison, setComparison] = useState<ComparisonResponse | null>(null);
  const [comparisonError, setComparisonError] = useState<string | null>(null);

  // Load the dropdown's product types at mount (usually already prefetched),
  // and again when the advisor presses Retry.
  const [typesAttempt, setTypesAttempt] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setTypesLoading(true);
    setTypesError(null);
    loadProductTypes()
      .then((types) => {
        if (!cancelled) {
          setProductTypes(types);
          setTypesError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setTypesError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (!cancelled) setTypesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [typesAttempt]);

  // When the user picks a type, refetch the available products.
  useEffect(() => {
    if (!selectedType) {
      setProductsForType([]);
      setSelectedIds([]);
      setProductsError(null);
      return;
    }
    let cancelled = false;
    setProductsLoading(true);
    setProductsError(null);
    setSelectedIds([]);
    setBrochure(null);
    setComparison(null);
    setComparisonError(null);
    loadProductsByType(selectedType)
      .then((products) => {
        if (!cancelled) {
          setProductsForType(products);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setProductsError(err instanceof Error ? err.message : String(err));
          setProductsForType([]);
        }
      })
      .finally(() => {
        if (!cancelled) setProductsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedType]);

  useEffect(() => {
    setSelectedIds((prev) => prev.filter((id) => productsForType.some((p) => p.product_id === id)));
  }, [productsForType]);

  // The uploaded brochure counts as a product, but at least one must come
  // from the list (the backend enforces the same rule).
  const totalProducts = selectedIds.length + (brochure ? 1 : 0);
  const canCompare =
    totalProducts >= MIN_PRODUCTS &&
    totalProducts <= MAX_PRODUCTS &&
    selectedIds.length >= 1 &&
    !comparing;

  const toggleProduct = (productId: string, checked: boolean) => {
    setSelectedIds((prev) => {
      if (checked) {
        if (prev.includes(productId)) return prev;
        if (prev.length + (brochure ? 1 : 0) >= MAX_PRODUCTS) return prev;
        return [...prev, productId];
      }
      return prev.filter((id) => id !== productId);
    });
  };

  const handleCompare = async () => {
    if (!canCompare) return;
    setComparing(true);
    setComparison(null);
    setComparisonError(null);
    try {
      const result = await compareProducts(selectedIds, locale, brochure ? [brochure.temp_id] : []);
      setComparison(result);
    } catch (err: unknown) {
      setComparisonError(err instanceof Error ? err.message : String(err));
    } finally {
      setComparing(false);
    }
  };

  const handleClearSelection = () => {
    setSelectedIds([]);
    setBrochure(null);
    setComparison(null);
    setComparisonError(null);
  };

  const typeOptions = useMemo(() => {
    return typesForLine(productTypes, line).map((type) => ({
      value: type,
      label: t(`domain.policyType.${type}`, { defaultValue: type }),
    }));
  }, [productTypes, line, t]);

  // Switching line clears the product type, which in turn clears the products,
  // brochure and result (see the selectedType effect above).
  const handleLineChange = (next: ProductLine) => {
    if (next === line) return;
    setLine(next);
    setSelectedType('');
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto flex max-w-5xl flex-col gap-5 p-6 sm:p-8">
        {/* Page header */}
        <div className="flex items-start gap-3">
          <Scale className="mt-1 h-6 w-6 text-brand-2" />
          <div>
            <h2 className="text-2xl font-bold tracking-tight">
              <span className="text-brand-gradient">
                {t('assistant.pages.comparator.heading')}
              </span>
            </h2>
            <p className="mt-1 max-w-prose text-sm text-foreground-muted">
              {t('assistant.pages.comparator.intro')}
            </p>
          </div>
        </div>

        {/* Selector card */}
        <Card feature className="p-5 sm:p-6">
          <div className="flex flex-col gap-4">
            {typesError ? (
              <Alert variant="danger">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span>{t('common.errors.loadFailed', { message: typesError })}</span>
                  <Button variant="secondary" size="sm" onClick={() => setTypesAttempt((n) => n + 1)}>
                    {t('common.actions.retry')}
                  </Button>
                </div>
              </Alert>
            ) : null}

            <LineToggle value={line} onChange={handleLineChange} />

            <div>
              <label className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-foreground-muted">
                {t('assistant.pages.comparator.typeLabel')}
              </label>
              <div className="relative" aria-busy={typesLoading}>
              <Select
                value={selectedType}
                onChange={(e) => setSelectedType(e.target.value)}
                disabled={typesLoading || Boolean(typesError)}
              >
                <option value="">
                  {typesLoading
                    ? t('assistant.pages.comparator.typesLoading')
                    : t('assistant.pages.comparator.typePlaceholder')}
                </option>
                {typeOptions.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </Select>
              {typesLoading ? (
                <span className="pointer-events-none absolute right-9 top-1/2 -translate-y-1/2">
                  <Spinner size="sm" />
                </span>
              ) : null}
              </div>
            </div>

            {selectedType ? (
              <ProductSelector
                products={productsForType}
                loading={productsLoading}
                error={productsError}
                selectedIds={selectedIds}
                onToggle={toggleProduct}
                maxProducts={MAX_PRODUCTS - (brochure ? 1 : 0)}
              />
            ) : (
              <p className="text-sm italic text-foreground-muted">
                {t('assistant.pages.comparator.selectType')}
              </p>
            )}
            {selectedType ? (
              <BrochureUpload
                productType={selectedType}
                brochure={brochure}
                onChange={(b) => {
                  setBrochure(b);
                  setComparison(null);
                  setComparisonError(null);
                }}
                slotsFull={!brochure && selectedIds.length >= MAX_PRODUCTS}
              />
            ) : null}

            <div className="border-t border-border pt-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <span className="text-xs text-foreground-muted">
                  {t('assistant.pages.comparator.selectedCount', { count: totalProducts })}
                </span>
                <div className="flex items-center gap-2">
                  {totalProducts > 0 ? (
                    <Button variant="ghost" size="sm" onClick={handleClearSelection}>
                      {t('assistant.pages.comparator.clearButton')}
                    </Button>
                  ) : null}
                  <Button
                    variant="primary"
                    size="md"
                    onClick={handleCompare}
                    loading={comparing}
                    disabled={!canCompare}
                  >
                    {t('assistant.pages.comparator.compareButton')}
                  </Button>
                </div>
              </div>

              {totalProducts > 0 && totalProducts < MIN_PRODUCTS ? (
                <p className="mt-2 text-xs text-foreground-muted">
                  {t('assistant.pages.comparator.minProducts', { min: MIN_PRODUCTS })}
                </p>
              ) : null}
              {totalProducts === MAX_PRODUCTS ? (
                <p className="mt-2 text-xs text-foreground-muted">
                  {t('assistant.pages.comparator.maxProducts', { max: MAX_PRODUCTS })}
                </p>
              ) : null}
            </div>
          </div>
        </Card>

        {/* Comparing indicator */}
        {comparing ? (
          <Card className="p-8">
            <div className="flex flex-col items-center gap-3 text-center">
              <Spinner size="lg" />
              <p className="text-sm font-medium">
                {t('assistant.pages.comparator.comparing')}
              </p>
              <p className="text-xs text-foreground-muted">
                {t('assistant.pages.comparator.comparingHint')}
              </p>
            </div>
          </Card>
        ) : null}

        {/* Error state */}
        {comparisonError ? (
          <Alert variant="danger" title={t('assistant.pages.comparator.errorTitle')}>
            <div className="flex flex-col gap-2">
              <p>{comparisonError}</p>
              <div>
                <Button variant="ghost" size="sm" onClick={handleCompare}>
                  {t('assistant.pages.comparator.errorRetry')}
                </Button>
              </div>
            </div>
          </Alert>
        ) : null}

        {/* Result */}
        {comparison && !comparing ? <ComparisonView data={comparison} /> : null}
      </div>
    </div>
  );
}

interface LineToggleProps {
  value: ProductLine;
  onChange: (line: ProductLine) => void;
}

/**
 * Life / General / Health switch. A radio group: arrow keys move between the
 * options, and the selected one is the tab stop.
 */
function LineToggle({ value, onChange }: LineToggleProps) {
  const { t } = useTranslation();
  const refs = useRef<Record<ProductLine, HTMLButtonElement | null>>({
    life: null,
    general: null,
    health: null,
  });

  const move = (delta: number) => {
    const next = PRODUCT_LINES[(PRODUCT_LINES.indexOf(value) + delta + PRODUCT_LINES.length) % PRODUCT_LINES.length];
    onChange(next);
    refs.current[next]?.focus();
  };

  return (
    <div>
      <div
        id="comparator-line-label"
        className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-foreground-muted"
      >
        {t('assistant.pages.comparator.line.label')}
      </div>
      <div
        role="radiogroup"
        aria-labelledby="comparator-line-label"
        className="inline-flex items-center gap-1 rounded-full border border-border bg-background-muted/60 p-0.5"
      >
        {PRODUCT_LINES.map((option) => {
          const active = option === value;
          return (
            <button
              key={option}
              ref={(el) => {
                refs.current[option] = el;
              }}
              type="button"
              role="radio"
              aria-checked={active}
              tabIndex={active ? 0 : -1}
              onClick={() => onChange(option)}
              onKeyDown={(e) => {
                if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
                  e.preventDefault();
                  move(1);
                } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
                  e.preventDefault();
                  move(-1);
                }
              }}
              className={cn(
                'rounded-full px-4 py-1.5 text-sm font-medium transition-[background,color,box-shadow] duration-150',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
                active
                  ? 'nav-pill-active'
                  : 'text-foreground-muted hover:bg-background-elevated hover:text-foreground'
              )}
            >
              {t(`assistant.pages.comparator.line.${option}`)}
            </button>
          );
        })}
      </div>
    </div>
  );
}

interface ProductSelectorProps {
  products: CatalogProduct[];
  loading: boolean;
  error: string | null;
  selectedIds: string[];
  onToggle: (productId: string, checked: boolean) => void;
  /** How many list products can be ticked (4, or 3 once a brochure is added). */
  maxProducts: number;
}
function ProductSelector({
  products,
  loading,
  error,
  selectedIds,
  onToggle,
  maxProducts,
}: ProductSelectorProps) {
  const { t } = useTranslation();

  if (loading) {
    return (
      <div className="inline-flex items-center gap-2 text-sm text-foreground-muted">
        <Spinner size="sm" />
        {t('common.states.loading')}
      </div>
    );
  }

  if (error) {
    return (
      <Alert variant="danger">
        {t('common.errors.loadFailed', { message: error })}
      </Alert>
    );
  }

  if (products.length === 0) {
    return (
      <p className="text-sm text-foreground-muted">
        {t('assistant.pages.comparator.noProductsForType')}
      </p>
    );
  }

  const atMax = selectedIds.length >= maxProducts;

  return (
    <div>
      <div className="mb-1 text-sm font-semibold">
        {t('assistant.pages.comparator.productsLabel')}
      </div>
      <p className="mb-3 text-xs text-foreground-muted">
        {t('assistant.pages.comparator.productsHelp', {
          min: MIN_PRODUCTS,
          max: MAX_PRODUCTS,
        })}
      </p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {products.map((product) => {
          const isSelected = selectedIds.includes(product.product_id);
          const disabled = atMax && !isSelected;
          return (
            <label
              key={product.product_id}
              className={cn(
                'flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 text-sm transition-[background,border-color,box-shadow]',
                isSelected
                  ? 'border-brand-2/50 bg-brand-2/5 shadow-[var(--shadow-sm)] ring-1 ring-brand-2/20'
                  : 'border-border bg-background-elevated hover:border-border-strong hover:bg-background-muted',
                disabled && 'opacity-50 cursor-not-allowed'
              )}
            >
              <span
                className={cn(
                  'mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded border transition-colors',
                  isSelected
                    ? 'border-brand-2 bg-gradient-to-br from-brand-2 to-brand-1 text-white'
                    : 'border-border bg-background-elevated'
                )}
              >
                {isSelected ? <Check className="h-3 w-3" strokeWidth={3} /> : null}
              </span>
              <input
                type="checkbox"
                className="sr-only"
                checked={isSelected}
                disabled={disabled}
                onChange={(e) => onToggle(product.product_id, e.target.checked)}
              />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-medium text-foreground">
                    {product.carrier_name}
                  </span>
                  <span className="text-foreground-muted">— {product.product_name}</span>
                </div>
                {product.pricing_tier ? (
                  <Badge variant="info" className="mt-1.5">
                    {t('assistant.pages.comparator.tierBadge', {
                      tier: product.pricing_tier,
                    })}
                  </Badge>
                ) : null}
              </div>
            </label>
          );
        })}
      </div>
    </div>
  );
}

/**
 * Coerce an LLM-supplied comparison value into something React can render.
 * The schema asks the model for strings, but a stray number / object / array
 * would otherwise throw "Objects are not valid as a React child" and white-
 * screen the whole app. Normalize defensively.
 */
function toCellText(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.map(toCellText).join(', ');
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function ComparisonView({ data }: { data: ComparisonResponse }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;
  const anyStale = data.products.some((p) => p.stale);
  const uploadedProducts = data.products.filter((p) => p.uploaded);
  const ratings = summariseRatings(data.products);
  const outlookWord = t('assistant.pages.comparator.ratings.outlook');
  // Customer PDF: the printable view is mounted only while exporting.
  const advisorEmail = useAdvisorEmail();
  const printRef = useRef<HTMLDivElement>(null);
  const [exporting, setExporting] = useState(false);
  const [pdfError, setPdfError] = useState<string | null>(null);
  useEffect(() => {
    if (!exporting) return;
    let cancelled = false;
    (async () => {
      try {
        if (!printRef.current) throw new Error('Print view not ready');
        await exportBlocksToPdf(printRef.current, {
          filename: `product-comparison-${new Date().toISOString().slice(0, 10)}.pdf`,
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
  }, [exporting]);

  return (
    <Card feature className="relative overflow-hidden">
      {/* The on-screen "Internal use only" badge below
          stays. The diagonal watermark here applies only to the browser's own
          Print command (a screenshot-style hard copy of the advisor's screen).
          The "Download PDF" handout for the customer is built separately in
          ComparisonPrintView and carries no internal-use marking. */}
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 z-50 hidden items-center justify-center print:flex"
      >
        <span className="rotate-45 select-none text-6xl font-black uppercase tracking-widest text-black/10">
          {t('assistant.pages.comparator.internalUseOnly')}
        </span>
      </div>

      <div className="border-b border-border bg-gradient-to-r from-brand-2/10 via-brand-1/5 to-transparent p-5 sm:p-6">
        <div className="flex items-start justify-between gap-3">
          <h3 className="text-lg font-semibold">{toCellText(data.title)}</h3>
          <div className="flex shrink-0 flex-col items-end gap-2">
            <Badge variant="warning" className="uppercase tracking-wide">
              {t('assistant.pages.comparator.internalUseOnly')}
            </Badge>
            <Button
              variant="secondary"
              size="sm"
              disabled={exporting}
              onClick={() => {
                setPdfError(null);
                setExporting(true);
              }}
              title={t('assistant.pages.comparator.pdf.hint')}
            >
              <Download className="h-3.5 w-3.5" />
              {exporting
                ? t('assistant.pages.comparator.pdf.preparing')
                : t('assistant.pages.comparator.pdf.button')}
            </Button>
          </div>
        </div>
        {pdfError ? (
          <p className="mt-2 text-xs text-danger" role="alert">
            {t('assistant.pages.comparator.pdf.failed', { message: pdfError })}
          </p>
        ) : null}
        {data.summary ? (
          <div className="mt-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-foreground-muted">
              {t('assistant.pages.comparator.resultSummary')}
            </div>
            <p className="mt-1 whitespace-pre-wrap text-sm text-foreground-muted">
              {toCellText(data.summary)}
            </p>
          </div>
        ) : null}
      </div>

      <div className="space-y-6 p-5 sm:p-6">
        {anyStale ? (
          <Alert variant="warning">
            {t('assistant.pages.comparator.staleNotice')}
          </Alert>
        ) : null}
        {uploadedProducts.length > 0 ? (
          <Alert variant="info">
            {t('assistant.pages.comparator.uploadedNotice', {
              name: uploadedProducts.map((p) => toCellText(p.name)).join(', '),
            })}
          </Alert>
        ) : null}
        {ratings.hasAny ? (
          <section>
            <h4 className="mb-2 text-sm font-semibold uppercase tracking-wide">
              {t('assistant.pages.comparator.ratings.title')}
            </h4>
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="border-b border-border bg-background-muted/60">
                    <th className="w-[28%] px-3 py-2 text-left font-medium" />
                    {data.products.map((product) => (
                      <th
                        key={product.id}
                        className="border-l border-border px-3 py-2 text-left align-top font-medium"
                      >
                        <div className="font-semibold">{toCellText(product.name)}</div>
                        <div className="text-xs font-normal text-foreground-muted">
                          {toCellText(product.carrier)}
                        </div>
                        {product.uploaded ? (
                          <Badge variant="info" className="mt-1">
                            {t('assistant.pages.comparator.brochure.badge')}
                          </Badge>
                        ) : null}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {ratings.rows.map((row, rowIdx) => (
                    <tr
                      key={row.agency}
                      className={cn(
                        'border-b border-border last:border-0',
                        rowIdx % 2 === 1 && 'bg-background-muted/30'
                      )}
                    >
                      <td className="px-3 py-2 align-top font-semibold">{row.agency}</td>
                      {row.cells.map((r, idx) => (
                        <td
                          key={`${row.agency}-${idx}`}
                          className="border-l border-border px-3 py-2 align-top"
                        >
                          {r ? (
                            <>
                              <span className="font-semibold">{r.rating}</span>
                              {ratingDetail(r, outlookWord) ? (
                                <div className="text-xs text-foreground-muted">
                                  {ratingDetail(r, outlookWord)}
                                </div>
                              ) : null}
                            </>
                          ) : (
                            <span className="text-foreground-muted">
                              {t('assistant.pages.comparator.ratings.notStated')}
                            </span>
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-1.5 space-y-0.5 text-[11px] text-foreground-muted">
              {ratings.anyIllustrative ? (
                <p>{t('assistant.pages.comparator.ratings.illustrativeNote')}</p>
              ) : null}
              {ratings.anyFromBrochure ? (
                <p>{t('assistant.pages.comparator.ratings.brochureNote')}</p>
              ) : null}
              {ratings.asOf ? (
                <p>
                  {t('assistant.pages.comparator.ratings.asOf', {
                    date: fmtDate(ratings.asOf, locale),
                  })}
                </p>
              ) : null}
            </div>
          </section>
        ) : null}

        {(Array.isArray(data.sections) ? data.sections : []).map((section) => (
          <section key={section.title}>
            <h4 className="mb-2 text-sm font-semibold uppercase tracking-wide">
              {toCellText(section.title)}
            </h4>
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="border-b border-border bg-background-muted/60">
                    <th className="w-[28%] px-3 py-2 text-left font-medium" />
                    {data.products.map((product) => (
                      <th
                        key={product.id}
                        className="border-l border-border px-3 py-2 text-left font-medium align-top"
                      >
                        <div className="font-semibold">{toCellText(product.name)}</div>
                        <div className="text-xs font-normal text-foreground-muted">
                          {toCellText(product.carrier)}
                          {product.pricing_tier ? ` · ${toCellText(product.pricing_tier)}` : ''}
                        </div>
                        {product.last_updated ? (
                          <div
                            className={cn(
                              'mt-1 inline-flex items-center gap-1 text-[11px] font-normal',
                              product.stale ? 'text-warning' : 'text-foreground-muted'
                            )}
                          >
                            {product.stale ? (
                              <AlertTriangle className="h-3 w-3" />
                            ) : null}
                            {product.stale
                              ? t('assistant.pages.comparator.productStale', {
                                  days: STALE_AFTER_DAYS,
                                })
                              : t('assistant.pages.comparator.productUpdated', {
                                  date: fmtDate(product.last_updated, locale),
                                })}
                          </div>
                        ) : null}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(Array.isArray(section.rows) ? section.rows : []).map((row, rowIdx) => (
                    <tr
                      key={row.attribute}
                      className={cn(
                        'border-b border-border last:border-0',
                        rowIdx % 2 === 1 && 'bg-background-muted/30'
                      )}
                    >
                      <td className="px-3 py-2 align-top font-semibold">
                        {toCellText(row.attribute)}
                      </td>
                      {(Array.isArray(row.values) ? row.values : []).map((value, idx) => (
                        <td
                          key={`${row.attribute}-${idx}`}
                          className="border-l border-border px-3 py-2 align-top text-foreground"
                        >
                          {toCellText(value)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ))}

        <p className="border-t border-border pt-3 text-xs italic text-foreground-muted">
          {data.disclaimer ? toCellText(data.disclaimer) : t('assistant.pages.comparator.disclaimer')}
        </p>
      </div>
      {exporting ? (
        <ComparisonPrintView ref={printRef} data={data} advisorEmail={advisorEmail} />
      ) : null}
    </Card>
  );
}
