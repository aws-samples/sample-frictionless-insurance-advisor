import { ChangeEvent } from 'react';
import { Globe } from 'lucide-react';

import { useTranslation } from 'react-i18next';

import { SUPPORTED_LOCALES } from '../i18n/config';

/**
 * Compact dropdown that switches the UI locale. i18next's language detector
 * persists the choice to `localStorage` under `i18nextLng`, so the selection
 * survives reloads and future sessions.
 *
 * Styled as an icon-led pill that fits the topnav cluster.
 */
export function LanguageSwitcher() {
  const { i18n, t } = useTranslation();

  const handleChange = (event: ChangeEvent<HTMLSelectElement>) => {
    void i18n.changeLanguage(event.target.value);
  };

  return (
    <label className="relative inline-flex items-center">
      <Globe className="pointer-events-none absolute left-2.5 h-3.5 w-3.5 text-foreground-muted" />
      <span className="sr-only">{t('common.nav.language')}</span>
      <select
        value={i18n.resolvedLanguage ?? i18n.language}
        onChange={handleChange}
        className="h-8 appearance-none rounded-full border border-border bg-background-elevated/70 pl-7 pr-7 text-xs font-medium text-foreground transition-[border-color,background] hover:border-border-strong hover:bg-background-elevated focus-visible:outline-none focus-visible:border-ring/60 focus-visible:ring-2 focus-visible:ring-ring/30"
      >
        {SUPPORTED_LOCALES.map((locale) => (
          <option key={locale.code} value={locale.code}>
            {locale.label}
          </option>
        ))}
      </select>
      {/* tiny chevron via gradient corner */}
      <svg
        className="pointer-events-none absolute right-2 h-3 w-3 text-foreground-muted"
        viewBox="0 0 12 12"
        fill="none"
        aria-hidden
      >
        <path d="M3 4.5l3 3 3-3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </label>
  );
}
