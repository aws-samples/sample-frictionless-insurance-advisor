import { HTMLAttributes } from 'react';

import { cn } from '../lib/cn';

export type BadgeVariant =
  | 'neutral'
  | 'brand'
  | 'success'
  | 'warning'
  | 'danger'
  | 'info';

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  /** Small status dot before the label */
  dot?: boolean;
}

const VARIANT: Record<BadgeVariant, string> = {
  neutral: 'bg-background-muted text-foreground-muted border-border',
  brand:
    'bg-brand-2/10 text-brand-2 border-brand-2/30 dark:text-brand-2 dark:bg-brand-2/15',
  success:
    'bg-success/10 text-success border-success/30 dark:bg-success/15',
  warning:
    'bg-warning/10 text-warning border-warning/30 dark:bg-warning/15',
  danger:
    'bg-danger/10 text-danger border-danger/30 dark:bg-danger/15',
  info: 'bg-info/10 text-info border-info/30 dark:bg-info/15',
};

const DOT: Record<BadgeVariant, string> = {
  neutral: 'bg-foreground-muted',
  brand: 'bg-brand-2',
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
  info: 'bg-info',
};

export function Badge({
  variant = 'neutral',
  dot,
  className,
  children,
  ...rest
}: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium border',
        VARIANT[variant],
        className
      )}
      // nosemgrep: react-props-spreading -- typed forwarding via HTMLAttributes
      {...rest}
    >
      {dot ? (
        <span className={cn('h-1.5 w-1.5 rounded-full', DOT[variant])} />
      ) : null}
      {children}
    </span>
  );
}
