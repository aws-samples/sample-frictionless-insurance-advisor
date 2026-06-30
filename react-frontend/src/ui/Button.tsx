import { ButtonHTMLAttributes, forwardRef } from 'react';

import { cn } from '../lib/cn';

export type ButtonVariant =
  | 'primary'
  | 'secondary'
  | 'ghost'
  | 'subtle'
  | 'destructive'
  | 'link';
export type ButtonSize = 'sm' | 'md' | 'lg';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  fullWidth?: boolean;
}

const SIZE: Record<ButtonSize, string> = {
  sm: 'h-8 px-3 text-xs gap-1.5',
  md: 'h-9 px-4 text-sm gap-2',
  lg: 'h-11 px-5 text-sm gap-2',
};

const BASE =
  'inline-flex items-center justify-center font-medium rounded-md ' +
  'transition-[background,box-shadow,color,transform] duration-150 ' +
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background ' +
  'disabled:opacity-50 disabled:cursor-not-allowed select-none whitespace-nowrap';

const VARIANT: Record<ButtonVariant, string> = {
  // Filled brand gradient — the single primary CTA per view.
  primary:
    'text-white bg-gradient-to-r from-brand-2 to-brand-1 ' +
    'hover:brightness-110 hover:shadow-[var(--shadow-glow)] active:translate-y-px',
  secondary:
    'bg-background-elevated text-foreground border border-border ' +
    'hover:bg-background-muted hover:border-border-strong active:translate-y-px',
  ghost:
    'bg-transparent text-foreground-muted hover:text-foreground hover:bg-background-muted',
  subtle:
    'bg-background-muted text-foreground hover:bg-background-elevated border border-transparent hover:border-border',
  destructive:
    'bg-danger/10 text-danger border border-danger/30 hover:bg-danger/15 hover:border-danger/50',
  link:
    'bg-transparent text-foreground-muted hover:text-foreground underline-offset-4 hover:underline px-0 h-auto',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary',
    size = 'md',
    loading,
    fullWidth,
    className,
    children,
    disabled,
    ...rest
  },
  ref
) {
  return (
    <button
      ref={ref}
      className={cn(
        BASE,
        SIZE[size],
        VARIANT[variant],
        fullWidth && 'w-full',
        loading && 'cursor-progress',
        className
      )}
      disabled={disabled || loading}
      // nosemgrep: react-props-spreading -- typed forwarding via HTMLAttributes
      {...rest}
    >
      {loading ? (
        <span
          className="h-3.5 w-3.5 rounded-full border-2 border-current border-t-transparent animate-spin"
          aria-hidden
        />
      ) : null}
      {children}
    </button>
  );
});
