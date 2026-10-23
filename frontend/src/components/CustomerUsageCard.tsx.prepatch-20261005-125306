import { useEffect, useState } from "react";
import { api } from "../api/client";
import { PeriodSwitcher, UsageChart, type Period } from "./UsageChart";
import { btnSecondary } from "./Modal";
import type { CustomerUsage } from "../types";

const REFRESH_MS = 10_000;
// The live figure is polled far faster than the rest of the payload, because
// it is the only part that changes second to second -- and because each call
// is what keeps the router connection open (see network/live_broker.py on the
// backend). Stop polling and the connection closes on its own.
const LIVE_REFRESH_MS = 1_000;

// Month-to-date usage and current throughput for one customer.
//
// Unlike LiveBandwidthWidget this makes no router call of its own -- it
// reads what poll_live_traffic already collected for every session at
// once, so ten staff members watching ten customers costs the router
// nothing extra. Each session says which source its speed came from:
// "router" is live to the second, "accounting" is an average over the
// NAS's reporting interval (the fallback when a router has no API access
// or the poller isn't running).
//
// Totals always come from accounting -- a router's interface counters
// restart with every session, so they can measure speed but never
// consumption.

function formatBytes(bytes: number | null) {
  if (bytes == null) return "—";
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(2)} GB`;
  const mb = bytes / 1024 ** 2;
  if (mb >= 1) return `${mb.toFixed(1)} MB`;
  return `${(bytes / 1024).toFixed(0)} KB`;
}

// Both halves of a down/up pair in the same unit, chosen from the larger --
// formatting them independently gives "0 Mbps · 1 kbps", which reads like a
// fault rather than an idle line.
function formatBpsPair(down: number, up: number) {
  const useMbps = Math.max(down, up) >= 1_000_000;
  const fmt = (v: number) =>
    useMbps ? `${(v / 1_000_000).toFixed(1)} Mbps` : `${Math.round(v / 1000)} kbps`;
  return { down: fmt(down), up: fmt(up) };
}

export function CustomerUsageCard({
  customerId,
  usageToken,
  onTokenChanged,
  liveBandwidthPublic,
  onLiveBandwidthPublicChanged,
}: {
  customerId: number;
  usageToken: string | null;
  onTokenChanged: (token: string) => void;
  liveBandwidthPublic: boolean;
  onLiveBandwidthPublicChanged: (value: boolean) => void;
}) {
  const [usage, setUsage] = useState<CustomerUsage | null>(null);
  // Which window the chart shows. Separate from the live "right now" figures
  // above it, which are always current regardless.
  const [period, setPeriod] = useState<Period>("month");
  // Held separately from `usage` so the fast poll can refresh the throughput
  // figure without replacing the history chart under the reader's cursor.
  const [live, setLive] = useState<CustomerUsage["live_sessions"] | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [regenerating, setRegenerating] = useState(false);

  useEffect(() => {
    let cancelled = false;

    function load() {
      api
        .get<CustomerUsage>(`/customers/${customerId}/usage/?period=${period}`)
        .then((res) => {
          if (!cancelled) {
            setUsage(res.data);
            setError("");
          }
        })
        .catch(() => {
          if (!cancelled) setError("Couldn't load usage for this customer.");
        });
    }

    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [customerId, period]);

  // Fast poll: live throughput only, and only while this tab is actually on
  // screen. Without the visibility check a backgrounded tab left open
  // overnight would hold a router connection open all night for nobody.
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    function tick() {
      if (document.hidden) return;
      api
        .get<{ live_sessions: CustomerUsage["live_sessions"] }>(`/customers/${customerId}/live/`)
        .then((res) => {
          if (!cancelled) setLive(res.data.live_sessions);
        })
        .catch(() => {
          /* A dropped poll is not worth a banner -- the next one is a second
             away, and the figure beside it says how fresh it is. */
        });
    }

    tick();
    timer = window.setInterval(tick, LIVE_REFRESH_MS);
    // Catch up immediately when the tab comes back rather than waiting a
    // whole interval on a figure that says "now".
    document.addEventListener("visibilitychange", tick);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [customerId]);

  const usageUrl = usageToken ? `${window.location.origin}/usage/${usageToken}` : null;

  async function copyLink() {
    if (!usageUrl) return;
    try {
      await navigator.clipboard.writeText(usageUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access can be blocked; the link is on screen to copy by hand.
      setCopied(false);
    }
  }

  async function toggleLiveBandwidthPublic(next: boolean) {
    // Optimistic: the switch should move under the finger. A failure puts it
    // back rather than leaving it lying about the state.
    onLiveBandwidthPublicChanged(next);
    try {
      await api.patch(`/customers/${customerId}/`, { live_bandwidth_public: next });
    } catch {
      onLiveBandwidthPublicChanged(!next);
      alert("Couldn't change that. Please try again.");
    }
  }

  async function regenerate() {
    if (
      !confirm(
        "Issue a new usage link?\n\nThe current link stops working immediately, including for the " +
          "customer if they've saved it. Use this if a link has been shared somewhere it shouldn't be."
      )
    )
      return;
    setRegenerating(true);
    try {
      const res = await api.post<{ usage_token: string }>(
        `/customers/${customerId}/regenerate-usage-link/`, {}
      );
      onTokenChanged(res.data.usage_token);
    } catch {
      alert("Couldn't issue a new link.");
    } finally {
      setRegenerating(false);
    }
  }

  if (error) return <p className="text-sm text-[var(--text-muted)]">{error}</p>;
  if (!usage) return <p className="text-sm text-[var(--text-muted)]">Loading usage…</p>;

  // The fast poll wins when it has an answer; the slower full payload is the
  // fallback for the first second before it arrives.
  const liveSessions = live ?? usage.live_sessions;
  const online = liveSessions.length > 0;
  const capPct =
    usage.cap_bytes && usage.cap_bytes > 0
      ? Math.min(100, Math.round((usage.total_bytes / usage.cap_bytes) * 100))
      : null;

  return (
    <div>
      <div className="grid gap-4 sm:grid-cols-3">
        <div>
          <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Used this month</p>
          <p className="text-2xl font-semibold tabular-nums text-[var(--text-primary)]">
            {formatBytes(usage.total_bytes)}
          </p>
          <p className="text-xs text-[var(--text-muted)]">
            {formatBytes(usage.download_bytes)} down · {formatBytes(usage.upload_bytes)} up
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
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Right now</p>
            {/* Staff decide, per customer, whether they see their own live
                speed -- in the portal and on their usage link alike. Off by
                default: it is the one thing on those pages that holds a
                router connection open, and the usage link needs no login. */}
            <label
              className="flex cursor-pointer items-center gap-1.5 text-xs text-[var(--text-muted)]"
              title={
                "When on, this customer sees a live speed figure in their portal and on the usage " +
                "link below. It holds a connection to their router open while they watch, so it is " +
                "off unless you turn it on, and it switches itself off after five minutes with " +
                "nobody watching. Their usage totals and history show either way."
              }
            >
              <span>Let them see live speed</span>
              <input
                type="checkbox"
                checked={liveBandwidthPublic}
                onChange={(e) => toggleLiveBandwidthPublic(e.target.checked)}
              />
            </label>
          </div>
          {liveBandwidthPublic && (
            // Said on screen, not just in a tooltip: staff who turn this on
            // should not have to remember to turn it off, and should not be
            // surprised when they come back and find it off.
            <p className="mt-1 text-right text-xs text-[var(--series-1)]">
              On — switches itself off after 5 minutes unwatched
            </p>
          )}
          {online ? (
            liveSessions.map((s, i) => (
              <div key={i} className="mb-2">
                <p className="text-lg font-medium tabular-nums text-[var(--text-primary)]">
                  ↓ {formatBpsPair(s.download_bps, s.upload_bps).down} · ↑{" "}
                  {formatBpsPair(s.download_bps, s.upload_bps).up}
                </p>
                <p className="text-xs text-[var(--text-muted)]">
                  {s.ip_address ?? "no address"}
                  {s.rate_source === "router" && " · live from router"}
                  {s.rate_source === "accounting" && " · accounting average"}
                  {!s.rate_source && " · speed not sampled yet"}
                </p>
              </div>
            ))
          ) : (
            <p className="text-sm text-[var(--text-secondary)]">Not connected</p>
          )}
        </div>

        <div>
          <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Sessions this month</p>
          <p className="text-2xl font-semibold tabular-nums text-[var(--text-primary)]">{usage.sessions}</p>
        </div>
      </div>

      {/* The history, under the live figures rather than replacing them: the
          tiles answer "what are they doing now", this answers "what have they
          been doing". */}
      <div className="mt-5 border-t border-[var(--border-hairline)] pt-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs font-semibold text-[var(--text-primary)]">Usage over time</p>
          <PeriodSwitcher value={period} onChange={setPeriod} />
        </div>
        <UsageChart series={usage.series ?? null} measuringSince={usage.measuring_since} />
      </div>

      <div className="mt-5 border-t border-[var(--border-hairline)] pt-4">
        <p className="text-xs font-semibold text-[var(--text-primary)]">Customer's usage link</p>
        <p className="mt-1 max-w-2xl text-xs text-[var(--text-muted)]">
          Send this to the customer to let them check their own usage. It needs no login, so treat it
          like a password — anyone holding it can see this line's usage (and nothing else). Issue a new
          one to revoke it.
        </p>
        {usageUrl ? (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <code className="rounded bg-[var(--tint-hover)] px-2 py-1 font-mono text-xs text-[var(--text-secondary)]">
              {usageUrl}
            </code>
            <button type="button" className={btnSecondary} onClick={copyLink}>
              {copied ? "Copied" : "Copy"}
            </button>
            <button
              type="button"
              className="text-xs text-[var(--text-muted)] hover:underline disabled:opacity-50"
              disabled={regenerating}
              onClick={regenerate}
            >
              {regenerating ? "Issuing…" : "Issue new link"}
            </button>
          </div>
        ) : (
          <p className="mt-2 text-xs text-[var(--text-muted)]">No link yet — reload the customer.</p>
        )}
      </div>
    </div>
  );
}
