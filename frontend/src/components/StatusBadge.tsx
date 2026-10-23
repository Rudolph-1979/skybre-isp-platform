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
  // CustomerTask.Status.DONE. Without it a finished task rendered as
  // undifferentiated grey, i.e. the same as "unknown".
  done: { bg: "#e8f7e8", text: "#0ca30c", dot: "var(--status-good)" },
  sent: { bg: "#e8f7e8", text: "#0ca30c", dot: "var(--status-good)" },
  approved: { bg: "#e8f7e8", text: "#0ca30c", dot: "var(--status-good)" },
  // TicketEscalation.Decision.ACCEPTED -- NOC agreeing the issue is
  // theirs. Same colour as "approved": it's the same kind of decision.
  accepted: { bg: "#e8f7e8", text: "#0ca30c", dot: "var(--status-good)" },
  rejected: { bg: "#fbeaea", text: "#b32e2e", dot: "var(--status-critical)" },
  closed: { bg: "#f0efec", text: "#52514e", dot: "var(--text-muted)" },
  new: { bg: "#eef4fc", text: "#2a78d6", dot: "var(--series-1)" },
  quote: { bg: "#eef4fc", text: "#2a78d6", dot: "var(--series-1)" },
  draft: { bg: "#eef4fc", text: "#2a78d6", dot: "var(--series-1)" },
  finalized: { bg: "#e8f7e8", text: "#0ca30c", dot: "var(--status-good)" },
  proforma: { bg: "#f3ecfb", text: "#7c3aed", dot: "#7c3aed" },
  pending: { bg: "#fff6e5", text: "#a5730a", dot: "var(--status-warning)" },
  // A service paused on purpose (#211): off, but not a problem to chase.
  paused: { bg: "#f3ecfb", text: "#7c3aed", dot: "#7c3aed" },
  unpaid: { bg: "#fff6e5", text: "#a5730a", dot: "var(--status-warning)" },
  // Some money has landed on the invoice but not all of it -- distinct
  // from plain "unpaid" (nothing received) so finance can tell the two
  // apart at a glance. See InvoiceStatusBadge below: this key is never on
  // Invoice.status itself, only ever computed from paid_amount vs total.
  partially_paid: { bg: "#eef4fc", text: "#2a78d6", dot: "var(--series-1)" },
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
  // The three row kinds on a customer's combined Statement (see
  // CustomerDetailPage's statementRows) -- not a document Status, but
  // shown the same way so the Type column reads at a glance.
  invoice: { bg: "#eef4fc", text: "#2a78d6", dot: "var(--series-1)" },
  credit_note: { bg: "#f3ecfb", text: "#7c3aed", dot: "#7c3aed" },
  payment: { bg: "#e8f7e8", text: "#0ca30c", dot: "var(--status-good)" },
  unknown: { bg: "#f0efec", text: "#52514e", dot: "var(--text-muted)" },
};

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const style = STATUS_STYLES[status] ?? STATUS_STYLES.unknown;
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium capitalize"
      style={{ background: style.bg, color: style.text }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: style.dot }} />
      {label ?? status.replace(/_/g, " ")}
    </span>
  );
}

// Rudolph: "Invoices must be categorised as follow - Unpaid, Paid, Partially
// Paid and Overdue ... Partially Paid can be combined with Overdue
// (Partially - Overdue)". Invoice.status on the backend still only ever
// holds unpaid/paid/overdue/etc (see billing.Invoice.Status) -- nothing
// there needed to change, because "partially paid" is entirely a function
// of paid_amount vs total on an otherwise-unpaid/overdue invoice, and both
// of those fields were already on every Invoice payload. So this stays a
// display-only categorisation, computed here rather than carried as a
// fifth stored status the backend and every migration/report would then
// have to agree on too.
//
// CORRECTED 1 Oct 2026: this first version checked `invoice.status ===
// "overdue"` directly, which can essentially never be true -- Invoice.
// Status.OVERDUE is never actually assigned to a real invoice anywhere in
// the backend (see billing.overdue's module docstring and
// customers.filters.CustomerFilter.filter_overdue, which both already
// document this). "Overdue" has always been a derived, date-based fact
// -- still owed and past its due date -- not a stored one. It must be
// computed server-side, not here: "today" depends on a timezone, and a
// browser clock set to another one would disagree with every other
// overdue list in the app (see Invoice.is_overdue / customers'
// CustomerTask.is_overdue, which already does this). So this now reads
// the `is_overdue` flag the backend computes, rather than re-deriving
// (and getting wrong) its own notion of overdue from the status string.
export function InvoiceStatusBadge({
  invoice,
}: {
  invoice: { status: string; paid_amount: string; total: string; is_overdue?: boolean };
}) {
  const paid = parseFloat(invoice.paid_amount || "0");
  const total = parseFloat(invoice.total || "0");
  // A credit note's own total is negative (see CustomerDetailPage's
  // statementRows) and is always created already Paid -- `paid > 0` keeps
  // this from ever reading one as "partially paid".
  const partial = paid > 0 && paid < total;
  const overdue = invoice.is_overdue ?? false;
  if (partial && overdue) {
    return <StatusBadge status="overdue" label="Partially Paid - Overdue" />;
  }
  if (partial) {
    return <StatusBadge status="partially_paid" label="Partially Paid" />;
  }
  if (overdue) {
    return <StatusBadge status="overdue" label="Overdue" />;
  }
  return <StatusBadge status={invoice.status} />;
}
