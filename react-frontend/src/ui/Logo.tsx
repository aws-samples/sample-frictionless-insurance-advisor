import { cn } from '../lib/cn';

/**
 * Inline SVG mark for Unicorn Insurance. A stylised unicorn horn /
 * shield mash-up — neon brand gradient. Currentcolor doesn't apply
 * because we use the gradient defined inside the SVG itself.
 */
export function Logo({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label="Unicorn Insurance logo"
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
      {/* Unicorn horn — striped diagonal */}
      <path
        d="M11 21 L17 9 L21 11 L15 23 Z"
        fill="url(#ui-grad)"
      />
      <path d="M13 19 L18 11" stroke="white" strokeWidth="0.8" strokeLinecap="round" opacity="0.6" />
      <path d="M14.5 17 L19 11.5" stroke="white" strokeWidth="0.8" strokeLinecap="round" opacity="0.6" />
    </svg>
  );
}
