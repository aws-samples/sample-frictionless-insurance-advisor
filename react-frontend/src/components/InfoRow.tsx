interface InfoRowProps {
  icon: React.ReactNode;
  label: string;
  value: string | number | null | undefined;
}

/** One labelled value in a profile grid. Renders nothing when the value is empty (0 is shown). */
export function InfoRow({ icon, label, value }: InfoRowProps) {
  if (!value && value !== 0) return null;
  return (
    <div className="flex items-start gap-2.5 text-sm">
      <span className="mt-0.5 text-foreground-muted shrink-0">{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="text-xs uppercase tracking-wide text-foreground-muted">{label}</div>
        <div className="mt-0.5 text-foreground break-words">{value}</div>
      </div>
    </div>
  );
}
