import { Fragment, ReactNode, useEffect, useId, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { cn } from '../lib/cn';
import { Spinner } from './Spinner';

export interface MenuItem {
  id: string;
  label: string;
  /** Optional second line under the label. */
  hint?: string;
}

export interface MenuGroup {
  /** Heading shown above the group. Omit for a menu with a single, unlabelled group. */
  label?: string;
  items: MenuItem[];
}

interface MenuButtonProps {
  /** Button text. */
  label: string;
  icon?: ReactNode;
  /** Heading at the top of the open menu. */
  menuTitle?: string;
  groups: MenuGroup[];
  onSelect: (id: string) => void;
  /** Shows a spinner in place of the chevron and ignores clicks. */
  busy?: boolean;
  disabled?: boolean;
  /** The id of the item that is currently selected, if the menu reflects a choice. */
  activeId?: string | null;
  align?: 'left' | 'right';
  className?: string;
}

/**
 * A button that opens a short menu of choices.
 *
 * Keyboard: Enter, Space or ArrowDown opens it and focuses the first item; the
 * arrow keys move; Escape closes and returns focus to the button. Clicking
 * outside closes it. It is a menu button in the WAI-ARIA sense
 * (aria-haspopup="menu", role="menu", role="menuitem").
 */
export function MenuButton({
  label,
  icon,
  menuTitle,
  groups,
  onSelect,
  busy = false,
  disabled = false,
  activeId = null,
  align = 'left',
  className,
}: MenuButtonProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onPointer);
    return () => document.removeEventListener('mousedown', onPointer);
  }, [open]);

  // Focus the first item when the menu opens.
  useEffect(() => {
    if (!open) return;
    rootRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus();
  }, [open]);

  const items = () =>
    Array.from(rootRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? []);

  const onMenuKeyDown = (e: React.KeyboardEvent) => {
    const all = items();
    const index = all.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      all[(index + 1) % all.length]?.focus();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      all[(index - 1 + all.length) % all.length]?.focus();
    } else if (e.key === 'Home') {
      e.preventDefault();
      all[0]?.focus();
    } else if (e.key === 'End') {
      e.preventDefault();
      all[all.length - 1]?.focus();
    } else if (e.key === 'Escape' || e.key === 'Tab') {
      if (e.key === 'Escape') e.preventDefault();
      setOpen(false);
      if (e.key === 'Escape') buttonRef.current?.focus();
    }
  };

  const inactive = disabled || busy;

  return (
    <div ref={rootRef} className={cn('relative inline-block', className)}>
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        disabled={disabled}
        aria-busy={busy}
        onClick={() => {
          if (!inactive) setOpen((v) => !v);
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' && !inactive) {
            e.preventDefault();
            setOpen(true);
          }
        }}
        className={cn(
          'inline-flex h-9 items-center gap-2 rounded-md border border-border bg-background-elevated px-3 text-sm font-medium text-foreground',
          'transition-[background,border-color,box-shadow] duration-150 hover:border-border-strong hover:bg-background-muted',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50',
          'disabled:cursor-not-allowed disabled:opacity-50',
          open && 'border-brand-2/50 bg-background-muted'
        )}
      >
        {icon ? <span className="text-brand-2">{icon}</span> : null}
        <span>{label}</span>
        {busy ? <Spinner size="sm" /> : <ChevronDown className={cn('h-4 w-4 text-foreground-muted transition-transform', open && 'rotate-180')} />}
      </button>

      {open ? (
        <div
          id={menuId}
          role="menu"
          aria-label={menuTitle ?? label}
          onKeyDown={onMenuKeyDown}
          className={cn(
            'absolute z-30 mt-1.5 min-w-[15rem] max-w-[min(22rem,90vw)] overflow-hidden rounded-lg border border-border bg-background-elevated py-1 shadow-[var(--shadow-md)]',
            align === 'right' ? 'right-0' : 'left-0'
          )}
        >
          {menuTitle ? (
            <div className="px-3 pb-1 pt-1.5 text-[11px] font-semibold uppercase tracking-wide text-foreground-muted">
              {menuTitle}
            </div>
          ) : null}
          {groups.map((group, gi) => (
            <Fragment key={group.label ?? gi}>
              {gi > 0 ? <div className="my-1 border-t border-border" role="separator" /> : null}
              {group.label ? (
                <div className="px-3 pb-0.5 pt-1.5 text-[11px] font-semibold uppercase tracking-wide text-foreground-muted" role="presentation">
                  {group.label}
                </div>
              ) : null}
              {group.items.map((item) => {
                const active = item.id === activeId;
                return (
                  <button
                    key={item.id}
                    type="button"
                    role="menuitem"
                    tabIndex={-1}
                    onClick={() => {
                      setOpen(false);
                      buttonRef.current?.focus();
                      onSelect(item.id);
                    }}
                    className={cn(
                      'flex w-full flex-col items-start px-3 py-1.5 text-left text-sm transition-colors',
                      'hover:bg-background-muted focus-visible:bg-background-muted focus-visible:outline-none',
                      active ? 'font-semibold text-brand-2' : 'text-foreground'
                    )}
                  >
                    <span>{item.label}</span>
                    {item.hint ? <span className="text-xs font-normal text-foreground-muted">{item.hint}</span> : null}
                  </button>
                );
              })}
            </Fragment>
          ))}
        </div>
      ) : null}
    </div>
  );
}
