import type { AuditEvent } from "../types";

/**
 * Renders audit events as a readable list rather than a table.
 *
 * A table forces every row to the width of its widest cell, and a change
 * list is the widest cell there is -- "Allowed sections: (none) -> tickets,
 * customers, finance" against a row whose other columns are a name and a
 * timestamp. Stacking each event instead lets the change list take the room
 * it needs without stretching the fifty rows around it.
 */

const ACTION_STYLE: Record<string, string> = {
  created: "bg-[var(--status-good)]/12 text-[var(--status-good)]",
  updated: "bg-[var(--series-1)]/12 text-[var(--series-1)]",
  deleted: "bg-[var(--status-critical)]/12 text-[var(--status-critical)]",
  login: "bg-[var(--status-good)]/12 text-[var(--status-good)]",
  login_failed: "bg-[var(--status-critical)]/12 text-[var(--status-critical)]",
  logout: "bg-[var(--tint-hover)] text-[var(--text-muted)]",
};

// "billing.Service" is how it's stored -- unambiguous, and meaningless to
// read. The label is what staff call the thing.
const TYPE_LABEL: Record<string, string> = {
  "customers.Customer": "Customer",
  "customers.Partner": "Partner",
  "billing.Service": "Service",
  "billing.Tariff": "Tariff",
  "billing.Invoice": "Invoice",
  "billing.Payment": "Payment",
  "accounts.User": "Staff account",
  "tickets.Ticket": "Ticket",
  "network.NetworkSite": "Site",
  "network.Device": "Device",
};

export function formatEventTime(iso: string) {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function ActionChip({ event }: { event: AuditEvent }) {
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${
        ACTION_STYLE[event.action] ?? "bg-[var(--tint-hover)] text-[var(--text-muted)]"
      }`}
    >
      {event.action_display}
    </span>
  );
}

export function ActivityRow({ event, showTarget = true }: { event: AuditEvent; showTarget?: boolean }) {
  const typeLabel = TYPE_LABEL[event.target_type] ?? event.target_type;
  const isAuth = ["login", "login_failed", "logout"].includes(event.action);
  return (
    <li className="border-b border-[var(--border-hairline)] px-4 py-3 last:border-0">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <ActionChip event={event} />
        <span className="text-sm font-medium text-[var(--text-primary)]">
          {event.actor_name || "System"}
        </span>
        {showTarget && !isAuth && (
          <span className="text-sm text-[var(--text-secondary)]">
            {typeLabel}
            {event.target_label ? ` · ${event.target_label}` : ""}
          </span>
        )}
        <span className="ml-auto text-xs tabular-nums text-[var(--text-muted)]">
          {formatEventTime(event.created_at)}
          {event.ip_address ? ` · ${event.ip_address}` : ""}
        </span>
      </div>

      {event.detail && (
        <p className="mt-1 text-xs text-[var(--text-muted)]">{event.detail}</p>
      )}

      {event.changes.length > 0 && (
        <ul className="mt-2 space-y-1">
          {event.changes.map((c, i) => (
            <li key={`${c.field}-${i}`} className="text-xs text-[var(--text-secondary)]">
              <span className="font-medium text-[var(--text-primary)]">{c.label}:</span>{" "}
              <span className="text-[var(--text-muted)]">{c.from || "—"}</span>
              <span className="mx-1.5 text-[var(--text-muted)]">→</span>
              <span>{c.to || "—"}</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function ActivityList({
  events,
  loading,
  emptyMessage,
  showTarget = true,
}: {
  events: AuditEvent[];
  loading: boolean;
  emptyMessage: string;
  showTarget?: boolean;
}) {
  if (loading && events.length === 0) {
    return <p className="px-4 py-6 text-sm text-[var(--text-muted)]">Loading…</p>;
  }
  if (events.length === 0) {
    return <p className="px-4 py-6 text-sm text-[var(--text-muted)]">{emptyMessage}</p>;
  }
  return (
    <ul className="divide-y-0">
      {events.map((e) => (
        <ActivityRow key={e.id} event={e} showTarget={showTarget} />
      ))}
    </ul>
  );
}
