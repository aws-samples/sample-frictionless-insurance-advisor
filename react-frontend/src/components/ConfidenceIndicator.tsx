import { cn } from '../lib/cn';
import type { FieldConfidence, FieldOrigin } from '../types';

interface ConfidenceIndicatorProps {
  tier: FieldConfidence;
  confidence: number;
  origin: FieldOrigin;
  /** The utterance the value came from, surfaced via the native tooltip. */
  source?: string;
}

/**
 * Small status dot shown beside a populated field.
 *
 * brand  — carried over from the customer's profile
 * green  — heard in the conversation, high confidence
 * amber  — heard but uncertain, worth a glance
 * grey   — the advisor typed or confirmed this
 *
 * The accessible name carries the same meaning as the colour, so the state
 * isn't conveyed by hue alone.
 */
export function ConfidenceIndicator({
  tier,
  confidence,
  origin,
  source,
}: ConfidenceIndicatorProps) {
  const { dotClass, label } =
    origin === 'profile'
      ? { dotClass: 'bg-brand-2', label: 'From customer profile' }
      : origin === 'manual'
        ? { dotClass: 'bg-foreground-muted', label: 'Entered by advisor' }
        : tier === 'high'
          ? {
              dotClass: 'bg-success',
              label: `Heard in conversation — high confidence (${Math.round(confidence * 100)}%)`,
            }
          : {
              dotClass: 'bg-warning',
              label: `Heard in conversation — please confirm (${Math.round(confidence * 100)}%)`,
            };

  const title = source ? `${label}\nHeard: “${source}”` : label;

  return (
    <span
      className={cn('inline-block h-1.5 w-1.5 shrink-0 rounded-full', dotClass)}
      role="img"
      aria-label={label}
      title={title}
    />
  );
}
