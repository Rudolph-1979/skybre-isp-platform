const STATUS_STYLES: Record<string, { bg: string; text: string; dot: string }> = {
  active: { bg: "#e8f7e8", text: "#0ca30c", dot: "var(--status-good)" },
  online: { bg: "#e8f7e8", text: "#0ca30c", dot: "var(--status-good)" },
  paid: { bg: "#e8f7e8", text: "#0ca30c", dot: "var(--status-good)" },
  resolved: { bg: "#e8f7e8", text: "#0ca30c", dot: "var(--status-good)" },
  sent: { bg: "#e8f7e8", text: "#0ca30c", dot: "var(--status-good)" },
  closed: { bg: "#f0efec", text: "#52514e", dot: "var(--text-muted)" },
  new: { bg: "#eef4fc", text: "#2a78d6", dot: "var(--series-1)" },
  pending: { bg: "#fff6e5", text: "#a5730a", dot: "var(--status-warning)" },
  unpaid: { bg: "#fff6e5", text: "#a5730a", dot: "var(--status-warning)" },
  open: { bg: "#fff6e5", text: "#a5730a", dot: "var(--status-warning)" },
  suspended: { bg: "#fdece9", text: "#c1512f", dot: "var(--status-serious)" },
  blocked: { bg: "#fbeaea", text: "#b32e2e", dot: "var(--status-critical)" },
  offline: { bg: "#fbeaea", text: "#b32e2e", dot: "var(--status-critical)" },
  overdue: { bg: "#fbeaea", text: "#b32e2e", dot: "var(--status-critical)" },
  cancelled: { bg: "#fbeaea", text: "#b32e2e", dot: "var(--status-critical)" },
  terminated: { bg: "#fbeaea", text: "#b32e2e", dot: "var(--status-critical)" },
  failed: { bg: "#fbeaea", text: "#b32e2e", dot: "var(--status-critical)" },
  urgent: { bg: "#fbeaea", text: "#b32e2e", dot: "var(--status-critical)" },
  high: { bg: "#fdece9", text: "#c1512f", dot: "var(--status-serious)" },
  medium: { bg: "#fff6e5", text: "#a5730a", dot: "var(--status-warning)" },
  low: { bg: "#f0efec", text: "#52514e", dot: "var(--text-muted)" },
  inactive: { bg: "#f0efec", text: "#52514e", dot: "var(--text-muted)" },
  unknown: { bg: "#f0efec", text: "#52514e", dot: "var(--text-muted)" },
};

export function StatusBadge({ status }: { status: string }) {
  const style = STATUS_STYLES[status] ?? STATUS_STYLES.unknown;
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium capitalize"
      style={{ background: style.bg, color: style.text }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: style.dot }} />
      {status.replace("_", " ")}
    </span>
  );
}
