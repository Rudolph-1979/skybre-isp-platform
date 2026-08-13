interface StatCardProps {
  label: string;
  value: string | number;
  sublabel?: string;
  accent?: "series-1" | "series-3" | "status-good" | "status-critical" | "status-warning";
}

export function StatCard({ label, value, sublabel, accent = "series-1" }: StatCardProps) {
  return (
    <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
      <div className="flex items-center gap-2">
        <span className="h-2 w-2 rounded-full" style={{ background: `var(--${accent})` }} />
        <p className="text-sm text-[var(--text-secondary)]">{label}</p>
      </div>
      <p className="tabular-nums mt-2 text-2xl font-semibold text-[var(--text-primary)]">{value}</p>
      {sublabel && <p className="mt-1 text-xs text-[var(--text-muted)]">{sublabel}</p>}
    </div>
  );
}
