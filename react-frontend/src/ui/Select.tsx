import { SelectHTMLAttributes, forwardRef } from 'react';
import { ChevronDown } from 'lucide-react';

import { cn } from '../lib/cn';

type SelectProps = SelectHTMLAttributes<HTMLSelectElement>;

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, children, ...rest },
  ref
) {
  return (
    <div className="relative inline-flex w-full">
      <select
        ref={ref}
        className={cn(
          'h-10 w-full appearance-none rounded-md border border-border bg-background-elevated pl-3 pr-9 text-sm text-foreground',
          'transition-[border-color,box-shadow] duration-150',
          'focus-visible:outline-none focus-visible:border-ring/60 focus-visible:ring-2 focus-visible:ring-ring/30',
          'disabled:opacity-50 disabled:cursor-not-allowed',
          className
        )}
        // nosemgrep: react-props-spreading -- typed forwarding via HTMLAttributes
        {...rest}
      >
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-foreground-muted" />
    </div>
  );
});
