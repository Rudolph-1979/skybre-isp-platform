import { useEffect, useState } from "react";
import { api } from "../api/client";
import { ActivityList } from "./ActivityFeed";
import { Table, THead, TH, TR, TD } from "./Table";
import type { AuditEvent, CustomerSession } from "../types";

/**
 * Two logs about the same customer, side by side rather than merged.
 *
 * They answer different questions and merging them helps neither: "who
 * changed this account" is a short list read top to bottom, while "was he
 * online last night" is a dense one people scan by date. Interleaving them
 * would bury four staff edits in three hundred reconnections.
 */

function formatBytes(bytes: number) {
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(2)} GB`;
  const mb = bytes / 1024 ** 2;
  if (mb >= 1) return `${mb.toFixed(1)} MB`;
  return `${(bytes / 1024).toFixed(0)} KB`;
}

function formatDuration(seconds: number | null) {
  if (seconds == null) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h >= 24) {
    const d = Math.floor(h / 24);
    return `${d}d ${h % 24}h`;
  }
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function formatWhen(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// RADIUS terminate causes are protocol constants. Left raw they read as
// shouting acronyms on a support screen; these are the ones this network
// actually produces.
const CAUSE_LABEL: Record<string, string> = {
  "User-Request": "Customer disconnected",
  "Lost-Carrier": "Line dropped",
  "Lost-Service": "Line dropped",
  "Idle-Timeout": "Idle timeout",
  "Session-Timeout": "Session timeout",
  "Admin-Reset": "Reset by us",
  "Admin-Reboot": "Router rebooted",
  "NAS-Request": "Router ended it",
  "NAS-Reboot": "Router rebooted",
  "Port-Error": "Port error",
  "Service-Unavailable": "Service unavailable",
};

const DAY_OPTIONS = [7, 30, 90];

export function CustomerHistoryTab({ customerId }: { customerId: number }) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [sessions, setSessions] = useState<CustomerSession[]>([]);
  const [loadingEvents, setLoadingEvents] = useState(true);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [days, setDays] = useState(30);
  const [error, setError] = useState("");

  useEffect(() => {
    setLoadingEvents(true);
    api
      .get<{ results: AuditEvent[] }>(`/customers/${customerId}/history/`)
      .then((res) => setEvents(res.data.results))
      .catch(() => setError("Couldn't load the change log."))
      .finally(() => setLoadingEvents(false));
  }, [customerId]);

  useEffect(() => {
    setLoadingSessions(true);
    api
      .get<{ results: CustomerSession[] }>(`/customers/${customerId}/sessions/?days=${days}`)
      .then((res) => setSessions(res.data.results))
      .catch(() => setError("Couldn't load the session log."))
      .finally(() => setLoadingSessions(false));
  }, [customerId, days]);

  return (
    <div className="space-y-8">
      {error && (
        <p className="rounded-md bg-[var(--status-critical)]/10 px-3 py-2 text-sm text-[var(--status-critical)]">
          {error}
        </p>
      )}

      <section>
        <h2 className="mb-1 text-sm font-semibold text-[var(--text-primary)]">Change log</h2>
        <p className="mb-3 text-xs text-[var(--text-muted)]">
          Every edit to this customer and their services, invoices, payments and tickets — who
          made it and what it was before.
        </p>
        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)]">
          <ActivityList
            events={events}
            loading={loadingEvents}
            emptyMessage="Nothing recorded yet. Changes are logged from the moment this went live — anything done before that isn't here."
          />
        </div>
      </section>

      <section>
        <div className="mb-1 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">Session log</h2>
          <div className="flex gap-1">
            {DAY_OPTIONS.map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setDays(d)}
                className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                  days === d
                    ? "bg-[var(--series-1)]/12 text-[var(--series-1)]"
                    : "text-[var(--text-muted)] hover:bg-[var(--tint-hover)]"
                }`}
              >
                {d} days
              </button>
            ))}
          </div>
        </div>
        <p className="mb-3 text-xs text-[var(--text-muted)]">
          Every time this customer's line connected and disconnected, straight from RADIUS
          accounting.
        </p>

        {loadingSessions && sessions.length === 0 ? (
          <p className="px-1 py-6 text-sm text-[var(--text-muted)]">Loading…</p>
        ) : sessions.length === 0 ? (
          <p className="px-1 py-6 text-sm text-[var(--text-muted)]">
            No sessions in the last {days} days.
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Connected</TH>
                <TH>Disconnected</TH>
                <TH>Duration</TH>
                <TH>IP</TH>
                <TH>Down</TH>
                <TH>Up</TH>
                <TH>Ended by</TH>
              </TR>
            </THead>
            <tbody>
              {sessions.map((s) => (
                <TR key={s.session_id}>
                  <TD>{formatWhen(s.started_at)}</TD>
                  <TD>
                    {s.active ? (
                      <span className="inline-flex items-center gap-1.5 text-[var(--status-good)]">
                        <span className="h-1.5 w-1.5 rounded-full bg-[var(--status-good)]" />
                        Still connected
                      </span>
                    ) : (
                      formatWhen(s.ended_at)
                    )}
                  </TD>
                  <TD className="tabular-nums">{formatDuration(s.duration_seconds)}</TD>
                  <TD className="tabular-nums">{s.ip_address || "—"}</TD>
                  <TD className="tabular-nums">{formatBytes(s.download_bytes)}</TD>
                  <TD className="tabular-nums">{formatBytes(s.upload_bytes)}</TD>
                  <TD>
                    {s.active
                      ? "—"
                      : CAUSE_LABEL[s.terminate_cause] ?? (s.terminate_cause || "—")}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          Data figures come from the router's accounting updates, which arrive every five
          minutes — a session that started moments ago will read 0 KB until the first one lands.
        </p>
      </section>
    </div>
  );
}
