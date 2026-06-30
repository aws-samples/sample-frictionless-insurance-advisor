import { LogOut, Mic, MessageSquare, Moon, Presentation, Scale, Sun } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { useTheme } from '../hooks/useTheme';
import { cn } from '../lib/cn';
import { IconButton, Logo } from '../ui';
import type { Page } from '../types';

import { LanguageSwitcher } from './LanguageSwitcher';

interface TopNavProps {
  activePage: Page;
  onChangePage: (page: Page) => void;
  advisorEmail: string | null;
  onSignOut: () => void;
}

const PAGES = [
  { id: 'assistant', labelKey: 'common.nav.assistant', Icon: MessageSquare },
  { id: 'voice', labelKey: 'common.nav.voice', Icon: Mic },
  { id: 'comparator', labelKey: 'common.nav.comparator', Icon: Scale },
] as const;

export function TopNav({ activePage, onChangePage, advisorEmail, onSignOut }: TopNavProps) {
  const { t } = useTranslation();
  const { theme, toggle } = useTheme();

  return (
    <header className="sticky top-0 z-40 border-b border-border glass">
      <div className="flex h-14 items-center justify-between gap-4 px-4 sm:px-6">
        {/* Brand + nav */}
        <div className="flex items-center gap-4 sm:gap-8">
          <div className="flex items-center gap-2.5">
            <Logo />
            <span className="hidden text-base font-bold tracking-tight sm:inline">
              <span className="text-brand-gradient">{t('common.brand.nameAccent')}</span>
              <span className="text-foreground-muted ml-1.5">{t('common.brand.nameSuffix')}</span>
            </span>
          </div>

          <nav className="flex items-center gap-1 rounded-full border border-border bg-background-muted/60 p-0.5">
            {PAGES.map(({ id, labelKey, Icon }) => {
              const isActive = id === activePage;
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => onChangePage(id)}
                  className={cn(
                    'inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium transition-[background,color,box-shadow] duration-150',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
                    isActive
                      ? 'nav-pill-active'
                      : 'text-foreground-muted hover:text-foreground hover:bg-background-elevated'
                  )}
                  aria-current={isActive ? 'page' : undefined}
                >
                  <Icon className="h-3.5 w-3.5" />
                  <span className="hidden sm:inline">{t(labelKey)}</span>
                </button>
              );
            })}
          </nav>
        </div>

        {/* Right cluster */}
        <div className="flex items-center gap-1.5 sm:gap-2">
          <LanguageSwitcher />
          <IconButton
            aria-label={t('common.nav.pitchDeck')}
            title={t('common.nav.pitchDeck')}
            variant="ghost"
            size="md"
            onClick={() => window.open('/presentation/index.html', '_blank', 'noopener,noreferrer')}
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
            <span className="hidden truncate text-xs text-foreground-muted md:inline-block max-w-[180px]">
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
