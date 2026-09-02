import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { btnSecondary, filterSelectClass } from "../../components/Modal";
import type { OfflineCustomers } from "../../types";

/**
 * A call list, not a report.
 *
 * Deliberately a page rather than a modal: this is worked THROUGH -- you ring
 * someone, they answer, you talk for four minutes, you come back. A modal
 * covers the rest of the platform for the whole of that, and closes if you
 * click the wrong thing. The tile on the dashboard sends you here.
 *
 * The phone number is the first thing on each row after the name, because
 * dialling it is the entire purpose. tel: and mailto: rather than plain text
 * so a softphone or a mail client picks it straight up.
 */

const WINDOWS = [
  { hours: 6, label: "Last 6 hours" },
  { hours: 24, label: "Last 24 hours" },
  { hours: 72, label: "Last 3 days" },
  { hours: 168, label: "Last week" },
];

function downFor(seconds: number) {
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min`;
  const hours = seconds / 3600;
  if (hours < 48) return `${hours.toFixed(hours < 10 ? 1 : 0)} hours`;
  return `${Math.round(hours / 24)} days`;
}

export function OfflineCustomersPage() {
  const [hours, setHours] = useState(24);
  const [data, setData] = useState<OfflineCustomers | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  // Purely local, and deliberately not persisted: it stops one agent ringing
  // the same person twice in a sitting. Sharing it between agents would need
  // a real "who is handling this" field, which is a bigger thing than a tick.
  const [called, setCalled] = useState<Set<number>>(new Set());
  // A counter, not a re-set of `hours`. Refresh used to call setHours(hours),
  // which React discards because the value is identical -- so the effect
  // never re-ran and the button did nothing at all.
  const [reloadNonce, setReloadNonce] = useState(0);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .get<OfflineCustomers>(`/offline-customers/?hours=${hours}`)
      .then((res) => {
        if (cancelled) return;
        setData(res.data);
        setUpdatedAt(new Date());
        setError("");
      })
      .catch(() => {
        if (!cancelled) setError("Couldn't load the offline list.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [hours, reloadNonce]);

  // Re-check on its own every minute. Someone whose line comes back should
  // drop off the list before an agent dials them, and an agent working
  // through it shouldn't have to remember to press anything.
  useEffect(() => {
    const timer = window.setInterval(() => setReloadNonce((n) => n + 1), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  const rows = data?.results ?? [];

  return (
    <div>
      <PageHeader
        title="Recently offline"
        subtitle="Customers whose line went down and hasn't come back. Longest down first."
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <select
          className={filterSelectClass}
          value={hours}
          onChange={(e) => setHours(Number(e.target.value))}
        >
          {WINDOWS.map((w) => (
            <option key={w.hours} value={w.hours}>{w.label}</option>
          ))}
        </select>
        {data && (
          <span className="text-sm text-[var(--text-muted)]">
            {rows.length} customer{rows.length === 1 ? "" : "s"} offline
          </span>
        )}
        {/* The timestamp is the feedback. Even working correctly, a refresh
            that returns in 40ms looks identical to one that did nothing --
            a clock that visibly moves is what tells you it happened. */}
        <span className="ml-auto text-xs text-[var(--text-muted)]">
          {updatedAt ? `Updated ${updatedAt.toLocaleTimeString()}` : ""}
        </span>
        <button
          type="button"
          className={btnSecondary}
          disabled={loading}
          onClick={() => setReloadNonce((n) => n + 1)}
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {/* Said once, at the top, so nobody wonders why a customer they know is
          off isn't here. */}
      <p className="mb-4 max-w-3xl text-xs text-[var(--text-muted)]">
        Suspended customers are left out — they're offline because we suspended them, and that isn't
        a fault to call about. So is anyone whose line already came back on its own.
      </p>

      {error ? (
        <p className="text-sm text-[var(--status-critical)]">{error}</p>
      ) : loading && !data ? (
        // Only on the FIRST load. A refresh -- and one fires on its own every
        // minute -- must not blank the table an agent is working through.
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="rounded-md border border-[var(--border-hairline)] bg-[var(--surface-2)] px-3 py-4 text-sm text-[var(--text-secondary)]">
          <strong className="text-[var(--text-primary)]">Everyone is online.</strong> No active
          customer has dropped in this period without coming back.
        </p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Customer</TH>
              <TH>Contact</TH>
              <TH>Offline for</TH>
              <TH>What happened</TH>
              <TH>Called</TH>
            </tr>
          </THead>
          <tbody>
            {rows.map((r) => (
              <TR key={r.customer} className={called.has(r.customer) ? "opacity-55" : undefined}>
                <TD>
                  <Link
                    to={`/admin/customers/${r.customer}`}
                    className="font-medium text-[var(--series-1)] hover:underline"
                  >
                    {r.full_name}
                  </Link>
                  <div className="text-xs text-[var(--text-muted)]">
                    {r.customer_ref} · {r.username}
                  </div>
                </TD>
                <TD>
                  {r.phone ? (
                    <a
                      href={`tel:${r.phone.replace(/\s/g, "")}`}
                      className="block text-sm text-[var(--series-1)] hover:underline"
                    >
                      {r.phone}
                    </a>
                  ) : (
                    <span className="block text-sm text-[var(--text-muted)]">No phone number</span>
                  )}
                  {r.email && (
                    <a
                      href={`mailto:${r.email}?subject=${encodeURIComponent(
                        "Your Skybre connection"
                      )}&body=${encodeURIComponent(
                        `Hi ${r.full_name.split(" ")[0]},\n\nWe noticed your connection has been ` +
                          `offline for ${downFor(r.offline_seconds)}. Is everything alright, or ` +
                          `can we help you get it back up?\n\nSkybre`
                      )}`}
                      className="block text-xs text-[var(--text-muted)] hover:underline"
                    >
                      {r.email}
                    </a>
                  )}
                </TD>
                <TD className="whitespace-nowrap">
                  <span className="font-medium tabular-nums">{downFor(r.offline_seconds)}</span>
                  <div className="text-xs text-[var(--text-muted)]">
                    since {new Date(r.last_seen).toLocaleString(undefined, {
                      day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
                    })}
                  </div>
                </TD>
                <TD>
                  <span className="text-sm">{r.terminate_reason || "Unknown"}</span>
                  {r.drops_in_period > 1 && (
                    // Flapping is a different fault from a clean outage, and
                    // it changes what you check first.
                    <div className="text-xs text-[var(--status-warning)]">
                      Dropped {r.drops_in_period} times in this period
                    </div>
                  )}
                  {r.last_ip && (
                    <div className="font-mono text-xs text-[var(--text-muted)]">{r.last_ip}</div>
                  )}
                </TD>
                <TD onClick={(e) => e.stopPropagation()}>
                  <label className="flex cursor-pointer items-center gap-1.5 text-xs text-[var(--text-muted)]">
                    <input
                      type="checkbox"
                      checked={called.has(r.customer)}
                      onChange={(e) => {
                        const next = new Set(called);
                        if (e.target.checked) next.add(r.customer);
                        else next.delete(r.customer);
                        setCalled(next);
                      }}
                    />
                    Done
                  </label>
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {rows.length > 0 && (
        <p className="mt-3 text-xs text-[var(--text-muted)]">
          "Done" is only remembered while this page is open, and only for you — it's there so you
          don't ring the same person twice in one sitting, not as a record of who has been contacted.
        </p>
      )}
    </div>
  );
}
