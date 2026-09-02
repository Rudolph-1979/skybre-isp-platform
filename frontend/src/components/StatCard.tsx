interface StatCardProps {
  label: string;
  value: string | number;
  sublabel?: string;
  accent?: "series-1" | "series-3" | "status-good" | "status-critical" | "status-warning";
  /**
   * Makes the card interactive. When supplied, the card renders as a
   * <button> rather than a <div>, so it is keyboard-reachable and announced
   * as clickable — a div with an onClick is neither.
   */
  onClick?: () => void;
  /** What clicking does, e.g. "Show the list". Shown, and used for a11y. */
  actionLabel?: string;
}

const BASE = "rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm";

export function StatCard({
  label,
  value,
  sublabel,
  accent = "series-1",
  onClick,
  actionLabel,
}: StatCardProps) {
  const body = (
    <>
      <div className="flex items-center gap-2">
        <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: `var(--${accent})` }} />
        <p className="text-sm text-[var(--text-secondary)]">{label}</p>
      </div>
      <p className="tabular-nums mt-2 text-2xl font-semibold text-[var(--text-primary)]">{value}</p>
      {sublabel && <p className="mt-1 text-xs text-[var(--text-muted)]">{sublabel}</p>}
    </>
  );

  if (!onClick) {
    return <div className={BASE}>{body}</div>;
  }

  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={actionLabel ? `${label}. ${actionLabel}` : undefined}
      className={`${BASE} w-full cursor-pointer text-left transition hover:border-[var(--text-muted)] hover:bg-[var(--tint-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--series-1)]`}
    >
      {body}
      {actionLabel && (
        <p className="mt-1.5 text-xs font-medium text-[var(--series-1)]">{actionLabel} →</p>
      )}
    </button>
  );
}
