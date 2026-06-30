import { ButtonHTMLAttributes, forwardRef } from 'react';

import { cn } from '../lib/cn';

export type IconButtonVariant = 'ghost' | 'subtle' | 'primary' | 'destructive';
export type IconButtonSize = 'sm' | 'md' | 'lg';

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: IconButtonVariant;
  size?: IconButtonSize;
  /** Required for screen readers — buttons without text need this. */
  'aria-label': string;
}

const SIZE: Record<IconButtonSize, string> = {
  sm: 'h-8 w-8',
  md: 'h-9 w-9',
  lg: 'h-10 w-10',
};

const VARIANT: Record<IconButtonVariant, string> = {
  ghost:
    'text-foreground-muted hover:text-foreground hover:bg-background-muted',
  subtle:
    'bg-background-muted text-foreground border border-transparent hover:border-border',
  primary:
    'text-white bg-gradient-to-br from-brand-2 to-brand-1 hover:brightness-110 hover:shadow-[var(--shadow-glow)]',
  destructive:
    'bg-danger/10 text-danger border border-danger/30 hover:bg-danger/15',
};

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  function IconButton(
    { variant = 'ghost', size = 'md', className, children, ...rest },
    ref
  ) {
    return (
      <button
        ref={ref}
        className={cn(
          'inline-flex items-center justify-center rounded-md transition-[background,color,box-shadow] duration-150',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background',
          'disabled:opacity-50 disabled:cursor-not-allowed',
          SIZE[size],
          VARIANT[variant],
          className
        )}
        // nosemgrep: react-props-spreading -- typed forwarding via HTMLAttributes
        {...rest}
      >
        {children}
      </button>
    );
  }
);
