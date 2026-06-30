import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { cn } from '../lib/cn';

interface SidebarToggleProps {
  collapsed: boolean;
  onToggle: () => void;
}

/**
 * Floating chevron that collapses or expands the customer sidebar.
 *
 * Positioned absolutely against the parent's left edge so it appears to
 * "ride" the sidebar's right border. When collapsed, it sits on the main
 * content's left edge instead.
 */
export function SidebarToggle({ collapsed, onToggle }: SidebarToggleProps) {
  const { t } = useTranslation();
  const label = collapsed ? t('assistant.sidebar.expand') : t('assistant.sidebar.collapse');

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={label}
      title={label}
      className={cn(
        'absolute z-30 inline-flex h-7 w-7 items-center justify-center rounded-full border border-border bg-background-elevated text-foreground-muted shadow-[var(--shadow-sm)] transition-[background,color,transform,left] duration-200',
        'hover:text-foreground hover:bg-background-muted hover:border-border-strong',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
        'top-3',
        collapsed ? 'left-2' : 'left-[268px]'
      )}
    >
      {collapsed ? (
        <ChevronRight className="h-3.5 w-3.5" />
      ) : (
        <ChevronLeft className="h-3.5 w-3.5" />
      )}
    </button>
  );
}
