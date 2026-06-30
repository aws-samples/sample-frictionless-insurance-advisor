import { useEffect, useMemo, useState } from 'react';

import { Check, Scale } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import {
  compareProducts,
  loadProductsByType,
  loadProductTypes,
} from '../lib/api';
import { cn } from '../lib/cn';
import { Alert, Badge, Button, Card, Select, Spinner } from '../ui';
import type { CatalogProduct, ComparisonResponse } from '../types';

const MIN_PRODUCTS = 2;
const MAX_PRODUCTS = 4;

export function ComparatorPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;

  // Catalog dimensions
  const [productTypes, setProductTypes] = useState<string[]>([]);
  const [typesLoading, setTypesLoading] = useState<boolean>(true);
  const [typesError, setTypesError] = useState<string | null>(null);

  // Current selection state
  const [selectedType, setSelectedType] = useState<string>('');
  const [productsForType, setProductsForType] = useState<CatalogProduct[]>([]);
  const [productsLoading, setProductsLoading] = useState<boolean>(false);
  const [productsError, setProductsError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  // Comparison result state
  const [comparing, setComparing] = useState<boolean>(false);
  const [comparison, setComparison] = useState<ComparisonResponse | null>(null);
  const [comparisonError, setComparisonError] = useState<string | null>(null);

  // Load the dropdown's product types once at mount.
  useEffect(() => {
    let cancelled = false;
    setTypesLoading(true);
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
  }, []);

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

  const canCompare =
    selectedIds.length >= MIN_PRODUCTS && selectedIds.length <= MAX_PRODUCTS && !comparing;

  const toggleProduct = (productId: string, checked: boolean) => {
    setSelectedIds((prev) => {
      if (checked) {
        if (prev.includes(productId)) return prev;
        if (prev.length >= MAX_PRODUCTS) return prev;
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
      const result = await compareProducts(selectedIds, locale);
      setComparison(result);
    } catch (err: unknown) {
      setComparisonError(err instanceof Error ? err.message : String(err));
    } finally {
      setComparing(false);
    }
  };

  const handleClearSelection = () => {
    setSelectedIds([]);
    setComparison(null);
    setComparisonError(null);
  };

  const typeOptions = useMemo(() => {
    return productTypes.map((type) => ({
      value: type,
      label: t(`domain.policyType.${type}`, { defaultValue: type }),
    }));
  }, [productTypes, t]);

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
                {t('common.errors.loadFailed', { message: typesError })}
              </Alert>
            ) : null}

            <div>
              <label className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-foreground-muted">
                {t('assistant.pages.comparator.typeLabel')}
              </label>
              <Select
                value={selectedType}
                onChange={(e) => setSelectedType(e.target.value)}
                disabled={typesLoading || Boolean(typesError)}
              >
                <option value="">
                  {t('assistant.pages.comparator.typePlaceholder')}
                </option>
                {typeOptions.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </Select>
            </div>

            {selectedType ? (
              <ProductSelector
                products={productsForType}
                loading={productsLoading}
                error={productsError}
                selectedIds={selectedIds}
                onToggle={toggleProduct}
              />
            ) : (
              <p className="text-sm italic text-foreground-muted">
                {t('assistant.pages.comparator.selectType')}
              </p>
            )}

            <div className="border-t border-border pt-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <span className="text-xs text-foreground-muted">
                  {t('assistant.pages.comparator.selectedCount', { count: selectedIds.length })}
                </span>
                <div className="flex items-center gap-2">
                  {selectedIds.length > 0 ? (
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

              {selectedIds.length > 0 && selectedIds.length < MIN_PRODUCTS ? (
                <p className="mt-2 text-xs text-foreground-muted">
                  {t('assistant.pages.comparator.minProducts', { min: MIN_PRODUCTS })}
                </p>
              ) : null}
              {selectedIds.length === MAX_PRODUCTS ? (
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

interface ProductSelectorProps {
  products: CatalogProduct[];
  loading: boolean;
  error: string | null;
  selectedIds: string[];
  onToggle: (productId: string, checked: boolean) => void;
}

function ProductSelector({
  products,
  loading,
  error,
  selectedIds,
  onToggle,
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

  const atMax = selectedIds.length >= MAX_PRODUCTS;

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
  const { t } = useTranslation();

  return (
    <Card feature className="overflow-hidden">
      <div className="border-b border-border bg-gradient-to-r from-brand-2/10 via-brand-1/5 to-transparent p-5 sm:p-6">
        <h3 className="text-lg font-semibold">{toCellText(data.title)}</h3>
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
    </Card>
  );
}
