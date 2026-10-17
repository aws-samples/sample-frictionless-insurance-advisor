import { useCallback, useState } from 'react';
import { FileText, Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { APPLICATION_FORMS, loadApplicationForm } from '../lib/applicationForms';
import { GAP_LINES, PRODUCT_LINES, type GapLine } from '../lib/productLines';
import { Alert, MenuButton, type MenuGroup } from '../ui';
import type { FormSchema } from '../types';
import { RecommendationSection } from './RecommendationSection';

interface ProfileActionsProps {
  customerId: string;
  /** Opens a loaded application form in the left panel. */
  onOpenForm: (productType: string, schema: FormSchema) => void;
}

/**
 * The two actions at the top of a customer's profile.
 *
 * "Create gap analysis" runs the coverage-gap analysis for the chosen product
 * line (Life, General, Health or all) and shows it directly underneath. Nothing
 * runs until the advisor asks. "Open application form" lists the ten forms
 * grouped by line and opens the chosen one for this customer.
 */
export function ProfileActions({ customerId, onOpenForm }: ProfileActionsProps) {
  const { t } = useTranslation();
  const [gapLine, setGapLine] = useState<GapLine | null>(null);
  const [formBusy, setFormBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const gapGroups: MenuGroup[] = [
    {
      items: GAP_LINES.map((line) => ({ id: line, label: t(`domain.productLine.${line}`) })),
    },
  ];

  // Forms grouped by line, in the order the list defines.
  const formGroups: MenuGroup[] = PRODUCT_LINES.map((line) => ({
    label: t(`domain.productLine.${line}`),
    items: APPLICATION_FORMS.filter((f) => f.line === line).map((f) => ({ id: f.file, label: f.name })),
  }));

  const openForm = useCallback(
    async (file: string) => {
      const entry = APPLICATION_FORMS.find((f) => f.file === file);
      if (!entry) return;
      setFormBusy(true);
      setFormError(null);
      try {
        const schema = await loadApplicationForm(entry);
        onOpenForm(schema.product_type ?? entry.productType, schema);
      } catch (err: unknown) {
        setFormError(err instanceof Error ? err.message : String(err));
      } finally {
        setFormBusy(false);
      }
    },
    [onOpenForm]
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <MenuButton
          label={t('assistant.customer.actions.gapAnalysis.button')}
          icon={<Sparkles className="h-4 w-4" />}
          menuTitle={t('assistant.customer.actions.gapAnalysis.menuTitle')}
          groups={gapGroups}
          activeId={gapLine}
          onSelect={(id) => setGapLine(id as GapLine)}
        />
        <MenuButton
          label={t('assistant.customer.actions.form.button')}
          icon={<FileText className="h-4 w-4" />}
          menuTitle={t('assistant.customer.actions.form.menuTitle')}
          groups={formGroups}
          busy={formBusy}
          onSelect={openForm}
        />
      </div>

      {formError ? (
        <Alert variant="danger">{t('assistant.customer.actions.form.error', { message: formError })}</Alert>
      ) : null}

      {gapLine ? (
        <RecommendationSection
          // A new customer or a new scope is a new analysis; the cache keeps finished ones.
          key={`${customerId}|${gapLine}`}
          customerId={customerId}
          line={gapLine}
          onClose={() => setGapLine(null)}
        />
      ) : null}
    </div>
  );
}
