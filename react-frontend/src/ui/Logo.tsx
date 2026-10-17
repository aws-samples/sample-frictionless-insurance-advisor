import { cn } from '../lib/cn';

/**
 * Inline SVG mark for Awesome Insurance: a shield with a check mark, drawn in
 * the brand gradient. currentColor does not apply because the gradient is
 * defined inside the SVG itself.
 */
export function Logo({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label="Awesome Insurance logo"
      className={cn('h-7 w-7 shrink-0', className)}
    >
      <defs>
        <linearGradient id="ui-grad" x1="0" y1="0" x2="32" y2="32" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="rgb(var(--brand-1))" />
          <stop offset="50%" stopColor="rgb(var(--brand-2))" />
          <stop offset="100%" stopColor="rgb(var(--brand-3))" />
        </linearGradient>
      </defs>
      {/* Shield base */}
      <path
        d="M16 2 L28 6 V14 C28 22 22 28 16 30 C10 28 4 22 4 14 V6 Z"
        fill="url(#ui-grad)"
        opacity="0.18"
      />
      <path
        d="M16 2 L28 6 V14 C28 22 22 28 16 30 C10 28 4 22 4 14 V6 Z"
        fill="none"
        stroke="url(#ui-grad)"
        strokeWidth="1.6"
      />
      {/* Check mark */}
      <path
        d="M10.5 16.5 L14.5 20.5 L22 11.5"
        fill="none"
        stroke="url(#ui-grad)"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
