import { ReactNode } from 'react';
import { AlertCircle, CheckCircle2, Info, TriangleAlert, X } from 'lucide-react';

import { cn } from '../lib/cn';
import { IconButton } from './IconButton';

export type AlertVariant = 'info' | 'success' | 'warning' | 'danger';

interface AlertProps {
  variant?: AlertVariant;
  title?: ReactNode;
  children?: ReactNode;
  onDismiss?: () => void;
  className?: string;
}

const VARIANT: Record<AlertVariant, { wrap: string; iconColor: string; Icon: typeof Info }> = {
  info: {
    wrap: 'bg-info/5 border-info/30 text-foreground',
    iconColor: 'text-info',
    Icon: Info,
  },
  success: {
    wrap: 'bg-success/5 border-success/30 text-foreground',
    iconColor: 'text-success',
    Icon: CheckCircle2,
  },
  warning: {
    wrap: 'bg-warning/5 border-warning/30 text-foreground',
    iconColor: 'text-warning',
    Icon: TriangleAlert,
  },
  danger: {
    wrap: 'bg-danger/5 border-danger/30 text-foreground',
    iconColor: 'text-danger',
    Icon: AlertCircle,
  },
};

export function Alert({
  variant = 'info',
  title,
  children,
  onDismiss,
  className,
}: AlertProps) {
  const v = VARIANT[variant];
  const { Icon } = v;
  return (
    <div
      role="status"
      className={cn(
        'flex items-start gap-3 rounded-lg border px-4 py-3 text-sm',
        v.wrap,
        className
      )}
    >
      <Icon className={cn('mt-0.5 h-4 w-4 shrink-0', v.iconColor)} aria-hidden />
      <div className="flex-1 space-y-1">
        {title ? <div className="font-semibold">{title}</div> : null}
        {children ? <div className="text-foreground-muted">{children}</div> : null}
      </div>
      {onDismiss ? (
        <IconButton
          aria-label="Dismiss"
          variant="ghost"
          size="sm"
          onClick={onDismiss}
          className="-mr-1 -mt-1"
        >
          <X className="h-4 w-4" />
        </IconButton>
      ) : null}
    </div>
  );
}
