import { type KeyboardEvent } from 'react';
import { Database, Home, LogOut, Mic, MessageSquare, Moon, Presentation, Scale, Sun } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { useTheme } from '../hooks/useTheme';
import { SUPPORTED_LOCALES } from '../i18n/config';
import { cn } from '../lib/cn';
import { IconButton, Logo } from '../ui';
import type { Page } from '../types';

import { LanguageSwitcher } from './LanguageSwitcher';

interface TopNavProps {
  activePage: Page;
  onChangePage: (page: Page) => void;
  /** Logo / brand click: exactly what the Home tab does. */
  onHome: () => void;
  advisorEmail: string | null;
  onSignOut: () => void;
}

const PAGES = [
  { id: 'home', labelKey: 'assistant.sidebar.home', Icon: Home },
  { id: 'assistant', labelKey: 'common.nav.assistant', Icon: MessageSquare },
  { id: 'voice', labelKey: 'common.nav.voice', Icon: Mic },
  { id: 'comparator', labelKey: 'common.nav.comparator', Icon: Scale },
  { id: 'data', labelKey: 'common.nav.data', Icon: Database },
] as const;

/** Open the pitch deck in the app's current language (the deck reads ?lang=). */
function openPitchDeck(language: string | undefined): void {
  const base = (language ?? '').toLowerCase().split(/[-_]/)[0];
  const code = SUPPORTED_LOCALES.some((l) => l.code === base) ? base : 'en';
  window.open(`/presentation/index.html?lang=${code}`, '_blank', 'noopener,noreferrer');
}

/** Left/Right (and Home/End) move focus along the tab row; Enter or Space then activates the focused tab. */
function moveTabFocus(e: KeyboardEvent<HTMLElement>): void {
  const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
  if (!keys.includes(e.key)) return;
  const tabs = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>('button'));
  const at = tabs.indexOf(document.activeElement as HTMLButtonElement);
  if (at < 0) return;
  const next =
    e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : (at + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
  e.preventDefault();
  tabs[next]?.focus();
}

export function TopNav({ activePage, onChangePage, onHome, advisorEmail, onSignOut }: TopNavProps) {
  const { t, i18n } = useTranslation();
  const { theme, toggle } = useTheme();

  return (
    <header className="sticky top-0 z-40 border-b border-border glass">
      <div className="flex h-14 items-center justify-between gap-2 px-3 sm:gap-4 sm:px-6">
        {/* Brand + nav */}
        <div className="flex min-w-0 items-center gap-2 sm:gap-4 xl:gap-8">
          <button
            type="button"
            onClick={onHome}
            aria-label={t('assistant.sidebar.home')}
            title={t('assistant.sidebar.home')}
            className="flex items-center gap-2.5 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            <Logo />
            <span className="hidden text-base font-bold tracking-tight xl:inline">
              <span className="text-brand-gradient">{t('common.brand.nameAccent')}</span>
              <span className="text-foreground-muted ml-1.5">{t('common.brand.nameSuffix')}</span>
            </span>
          </button>

          <nav
            onKeyDown={moveTabFocus}
            className="flex items-center gap-0.5 rounded-full border border-border bg-background-muted/60 p-0.5 sm:gap-1"
          >
            {PAGES.map(({ id, labelKey, Icon }) => {
              const isActive = id === activePage;
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => onChangePage(id)}
                  className={cn(
                    'inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-1.5 text-xs font-medium transition-[background,color,box-shadow] duration-150 lg:px-2.5 xl:px-3',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
                    isActive
                      ? 'nav-pill-active'
                      : 'text-foreground-muted hover:text-foreground hover:bg-background-elevated'
                  )}
                  aria-current={isActive ? 'page' : undefined}
                  aria-label={t(labelKey)}
                  title={t(labelKey)}
                >
                  <Icon className="h-3.5 w-3.5" />
                  <span className="hidden lg:inline">{t(labelKey)}</span>
                </button>
              );
            })}
          </nav>
        </div>

        {/* Right cluster */}
        <div className="flex shrink-0 items-center gap-0.5 sm:gap-2">
          <LanguageSwitcher />
          <IconButton
            aria-label={t('common.nav.pitchDeck')}
            title={t('common.nav.pitchDeck')}
            variant="ghost"
            size="md"
            className="hidden sm:inline-flex"
            onClick={() => openPitchDeck(i18n.resolvedLanguage ?? i18n.language)}
          >
            <Presentation className="h-4 w-4" />
          </IconButton>
          <IconButton
            aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            variant="ghost"
            size="md"
            onClick={toggle}
          >
            {theme === 'dark' ? (
              <Sun className="h-4 w-4" />
            ) : (
              <Moon className="h-4 w-4" />
            )}
          </IconButton>

          {advisorEmail ? (
            <span className="hidden truncate text-xs text-foreground-muted xl:inline-block max-w-[180px]">
              {advisorEmail}
            </span>
          ) : null}
          <IconButton aria-label={t('common.nav.signOut')} variant="ghost" size="md" onClick={onSignOut}>
            <LogOut className="h-4 w-4" />
          </IconButton>
        </div>
      </div>

      <div className="brand-hairline" />
    </header>
  );
}
