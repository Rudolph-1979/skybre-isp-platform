import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { CustomerUsage } from "../types";

// Usage for one customer, as the customer sees it. Used on the portal
// dashboard; staff see the richer CustomerUsageCard on the admin side, and
// the no-login link page has its own standalone layout.
//
// This component only ever reads the API. Totals come from RADIUS
// accounting; the speed figures come from whichever source is fresher --
// the router poll (live, to the second) or the accounting average. Either
// way no router call happens per viewer, so the refresh below is cheap
// however many people are looking.

const REFRESH_MS = 10_000;
// The throughput figure is polled far faster than the rest, because it is the
// only part that changes second to second -- and because each call is what
// keeps the router connection open (network/live_broker.py on the backend).
// Stop polling and the connection closes on its own.
const LIVE_REFRESH_MS = 1_000;

function formatBytes(bytes: number | null) {
  if (bytes == null) return "—";
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(2)} GB`;
  const mb = bytes / 1024 ** 2;
  if (mb >= 1) return `${mb.toFixed(1)} MB`;
  return `${(bytes / 1024).toFixed(0)} KB`;
}

// Both halves of a down/up pair in the same unit, chosen from the larger,
// so an idle line doesn't read "0 Mbps · 1 kbps" and look broken.
function formatBpsPair(down: number, up: number) {
  const useMbps = Math.max(down, up) >= 1_000_000;
  const fmt = (v: number) =>
    useMbps ? `${(v / 1_000_000).toFixed(1)} Mbps` : `${Math.round(v / 1000)} kbps`;
  return { down: fmt(down), up: fmt(up) };
}

export function UsagePanel({ customerId }: { customerId: number }) {
  const [usage, setUsage] = useState<CustomerUsage | null>(null);
  const [failed, setFailed] = useState(false);
  // Held apart from `usage` so the fast poll can move the speed figure
  // without replacing the whole panel ten times a second.
  const [live, setLive] = useState<CustomerUsage["live_sessions"] | null>(null);

  useEffect(() => {
    let cancelled = false;

    function load() {
      api
        .get<CustomerUsage>(`/customers/${customerId}/usage/`)
        .then((res) => {
          if (!cancelled) {
            setUsage(res.data);
            setFailed(false);
          }
        })
        .catch(() => {
          if (!cancelled) setFailed(true);
        });
    }

    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [customerId]);

  // Fast poll for the live speed, and only while this tab is actually on
  // screen -- a backgrounded tab left open overnight must not hold a router
  // connection open for nobody.
  //
  // Returns an empty list unless staff have enabled live figures for this
  // customer, in which case the panel simply keeps showing the accounting
  // average from the slower payload.
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    function tick() {
      if (document.hidden) return;
      api
        .get<{ live_sessions: CustomerUsage["live_sessions"]; live_enabled?: boolean }>(
          `/customers/${customerId}/live/`
        )
        .then((res) => {
          if (cancelled) return;
          setLive(res.data.live_enabled === false ? null : res.data.live_sessions);
        })
        .catch(() => {
          /* One dropped poll is not worth telling a customer about; the next
             is a second away. */
        });
    }

    tick();
    timer = window.setInterval(tick, LIVE_REFRESH_MS);
    document.addEventListener("visibilitychange", tick);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [customerId]);

  // Deliberately renders nothing on failure rather than an error box: this
  // sits on a customer's dashboard, and "couldn't load usage" is noise to
  // someone who came to check their balance.
  if (failed) return null;
  if (!usage) return null;

  // The fast poll wins when it has an answer; the slower payload covers the
  // first second, and customers without live enabled.
  const liveSessions = live ?? usage.live_sessions;
  const online = liveSessions.length > 0;
  const capPct =
    usage.cap_bytes && usage.cap_bytes > 0
      ? Math.min(100, Math.round((usage.total_bytes / usage.cap_bytes) * 100))
      : null;
  const session = liveSessions[0];
  const rate = session ? formatBpsPair(session.download_bps, session.upload_bps) : null;

  return (
    <div className="mb-6 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold">Data usage</h2>
        <span className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
          <span
            className={`inline-block h-2 w-2 rounded-full ${online ? "bg-[#0ca30c]" : "bg-[var(--text-muted)]"}`}
          />
          {online ? "Connected" : "Not connected"}
        </span>
      </div>

      <div className="grid gap-5 sm:grid-cols-3">
        <div>
          <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Used this month</p>
          <p className="text-2xl font-semibold tabular-nums text-[var(--text-primary)]">
            {formatBytes(usage.total_bytes)}
          </p>
          {capPct != null && (
            <div className="mt-2">
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--tint-hover)]">
                <div className="h-full rounded-full bg-[var(--series-1)]" style={{ width: `${capPct}%` }} />
              </div>
              <p className="mt-1 text-xs text-[var(--text-muted)]">
                {capPct}% of {formatBytes(usage.cap_bytes)}
              </p>
            </div>
          )}
        </div>

        <div>
          <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Download now</p>
          <p className="text-2xl font-semibold tabular-nums text-[var(--text-primary)]">
            {rate ? rate.down : "—"}
          </p>
        </div>

        <div>
          <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Upload now</p>
          <p className="text-2xl font-semibold tabular-nums text-[var(--text-primary)]">
            {rate ? rate.up : "—"}
          </p>
        </div>
      </div>

      <p className="mt-4 border-t border-[var(--border-hairline)] pt-3 text-xs text-[var(--text-muted)]">
        {formatBytes(usage.download_bytes)} downloaded · {formatBytes(usage.upload_bytes)} uploaded
        {session?.rate_source === "accounting" && " · speeds shown are a short-term average"}
      </p>
    </div>
  );
}
