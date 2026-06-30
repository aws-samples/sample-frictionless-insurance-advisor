import { useMemo } from 'react';

import { cn } from '../lib/cn';

interface AvatarProps {
  name: string | null | undefined;
  size?: 'xs' | 'sm' | 'md' | 'lg';
  className?: string;
}

const SIZE = {
  xs: 'h-6 w-6 text-[10px]',
  sm: 'h-8 w-8 text-xs',
  md: 'h-10 w-10 text-sm',
  lg: 'h-14 w-14 text-base',
};

// Six neon-leaning gradient pairs. Stable per-name via a hash so a customer
// always gets the same avatar tint.
const GRADIENTS = [
  ['from-fuchsia-500', 'to-violet-500'],
  ['from-violet-500', 'to-sky-500'],
  ['from-sky-500', 'to-emerald-400'],
  ['from-emerald-400', 'to-fuchsia-500'],
  ['from-orange-400', 'to-rose-500'],
  ['from-amber-400', 'to-pink-500'],
] as const;

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function Avatar({ name, size = 'sm', className }: AvatarProps) {
  const safe = name ?? '?';
  const [from, to] = useMemo(() => GRADIENTS[hash(safe) % GRADIENTS.length], [safe]);
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-full font-semibold text-white shadow-[var(--shadow-sm)]',
        'bg-gradient-to-br',
        from,
        to,
        SIZE[size],
        className
      )}
      aria-hidden
    >
      {initials(safe)}
    </span>
  );
}
