const STATUS_STYLES: Record<string, { bg: string; text: string; dot: string }> = {
  active: { bg: "#e8f7e8", text: "#0ca30c", dot: "var(--status-good)" },
  ok: { bg: "#e8f7e8", text: "#0ca30c", dot: "var(--status-good)" },
  due_soon: { bg: "#fff6e5", text: "#a5730a", dot: "var(--status-warning)" },
  // Blue, not amber or red: it's being dealt with, which is the one thing
  // on this list that needs no chasing. Sharing a colour with the overdue
  // states would put it back in the pile it exists to come out of.
  in_service: { bg: "#eef4fc", text: "#2a78d6", dot: "var(--series-1)" },
  online: { bg: "#e8f7e8", text: "#0ca30c", dot: "var(--status-good)" },
  paid: { bg: "#e8f7e8", text: "#0ca30c", dot: "var(--status-good)" },
  resolved: { bg: "#e8f7e8", text: "#0ca30c", dot: "var(--status-good)" },
  sent: { bg: "#e8f7e8", text: "#0ca30c", dot: "var(--status-good)" },
  approved: { bg: "#e8f7e8", text: "#0ca30c", dot: "var(--status-good)" },
  rejected: { bg: "#fbeaea", text: "#b32e2e", dot: "var(--status-critical)" },
  closed: { bg: "#f0efec", text: "#52514e", dot: "var(--text-muted)" },
  new: { bg: "#eef4fc", text: "#2a78d6", dot: "var(--series-1)" },
  quote: { bg: "#eef4fc", text: "#2a78d6", dot: "var(--series-1)" },
  draft: { bg: "#eef4fc", text: "#2a78d6", dot: "var(--series-1)" },
  finalized: { bg: "#e8f7e8", text: "#0ca30c", dot: "var(--status-good)" },
  proforma: { bg: "#f3ecfb", text: "#7c3aed", dot: "#7c3aed" },
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
  // Its own colour, not a shade of suspended: writing off a debt is a
  // different decision from cutting somebody off, and the two must be
  // tellable apart at a glance in a list.
  bad_debt: { bg: "#f7e7e7", text: "#a32a2a", dot: "var(--status-critical)" },
  inactive: { bg: "#f0efec", text: "#52514e", dot: "var(--text-muted)" },
  // Stock unit states (inventory.SerializedUnit). Without these a unit's
  // status rendered as undifferentiated grey.
  in_stock: { bg: "#e8f7e8", text: "#0ca30c", dot: "var(--status-good)" },
  issued: { bg: "#eef4fc", text: "#2a78d6", dot: "var(--series-1)" },
  faulty: { bg: "#fbeaea", text: "#b32e2e", dot: "var(--status-critical)" },
  returned_to_supplier: { bg: "#f0efec", text: "#52514e", dot: "var(--text-muted)" },
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
      {status.replace(/_/g, " ")}
    </span>
  );
}
