import { HTMLAttributes, forwardRef } from 'react';

import { cn } from '../lib/cn';

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** Adds a subtle hover lift — for interactive cards (rows, policies). */
  interactive?: boolean;
  /** Lifted shadow + brand gradient hairline at the top — flagship cards. */
  feature?: boolean;
}

export const Card = forwardRef<HTMLDivElement, CardProps>(function Card(
  { interactive, feature, className, children, ...rest },
  ref
) {
  return (
    <div
      ref={ref}
      className={cn(
        'relative rounded-xl border border-border bg-background-elevated',
        'shadow-[var(--shadow-sm)]',
        interactive && 'card-hover cursor-pointer',
        feature && 'shadow-[var(--shadow-md)]',
        className
      )}
      // nosemgrep: react-props-spreading -- typed forwarding via HTMLAttributes
      {...rest}
    >
      {feature ? (
        <div
          className="absolute inset-x-0 top-0 h-px rounded-t-xl pointer-events-none"
          style={{
            background:
              'linear-gradient(90deg, rgb(var(--brand-1)), rgb(var(--brand-2)), rgb(var(--brand-3)))',
            opacity: 0.8,
          }}
        />
      ) : null}
      {children}
    </div>
  );
});
