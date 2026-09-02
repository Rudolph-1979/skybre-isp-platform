import { useEffect, useState } from "react";
import { PeriodSwitcher, UsageChart, type Period } from "../components/UsageChart";
import { useParams } from "react-router-dom";
import axios from "axios";
import skybreIcon from "../assets/skybre-icon.png";

// Deliberately NOT the shared `api` client: that one attaches the staff JWT
// from localStorage and redirects to /login on a 401. This page is opened by
// a customer from a link, with no account at all.
const publicApi = axios.create({ baseURL: "/api" });

type LiveSession = {
  started_at: string | null;
  ip_address: string | null;
  mac_address: string | null;
  download_bytes: number;
  upload_bytes: number;
  download_bps: number;
  upload_bps: number;
  rate_measured_at: string | null;
};

import type { UsageSeries } from "../types";

type UsagePayload = {
  series?: UsageSeries;
  measuring_since?: string | null;
  customer_name: string;
  period: { year: number; month: number };
  cap_bytes: number | null;
  download_bytes: number;
  upload_bytes: number;
  total_bytes: number;
  sessions: number;
  live_sessions: LiveSession[];
};

const REFRESH_MS = 15_000;

function formatBytes(bytes: number | null) {
  if (bytes == null) return "—";
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(2)} GB`;
  const mb = bytes / 1024 ** 2;
  if (mb >= 1) return `${mb.toFixed(1)} MB`;
  return `${(bytes / 1024).toFixed(0)} KB`;
}

// Both figures in a down/up pair are formatted in the SAME unit, chosen
// from the larger of the two. Formatting them independently produced
// "0 Mbps" sitting next to "1 kbps", which reads like a fault rather than
// an idle line.
function formatBpsPair(down: number, up: number) {
  const useMbps = Math.max(down, up) >= 1_000_000;
  const fmt = (v: number) =>
    useMbps ? `${(v / 1_000_000).toFixed(1)} Mbps` : `${Math.round(v / 1000)} kbps`;
  return { down: fmt(down), up: fmt(up) };
}

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

export function PublicUsagePage() {
  const { token } = useParams<{ token: string }>();
  const [data, setData] = useState<UsagePayload | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [period, setPeriod] = useState<Period>("month");

  useEffect(() => {
    let cancelled = false;

    function load() {
      publicApi
        .get<UsagePayload>(`/public/usage/${token}/?period=${period}`)
        .then((res) => {
          if (cancelled) return;
          setData(res.data);
          setError("");
          setLoading(false);
        })
        .catch((err) => {
          if (cancelled) return;
          setError(
            err?.response?.status === 404
              ? "This usage link isn't valid. It may have been replaced — ask us for a new one."
              : "Couldn't load your usage just now. Please try again shortly."
          );
          setLoading(false);
        });
    }

    load();
    // The speed figures are refreshed by the router poller every ~10s, so
    // this is roughly in step with the underlying data. Faster would just
    // re-fetch the same numbers.
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [token, period]);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--surface-0)] p-6">
        <p className="text-[var(--text-muted)]">Loading your usage…</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--surface-0)] p-6">
        <div className="max-w-md rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-6 text-center">
          <p className="text-sm text-[var(--text-secondary)]">{error}</p>
        </div>
      </div>
    );
  }

  const online = data.live_sessions.length > 0;
  const capPct =
    data.cap_bytes && data.cap_bytes > 0
      ? Math.min(100, Math.round((data.total_bytes / data.cap_bytes) * 100))
      : null;

  return (
    <div className="min-h-screen bg-[var(--surface-0)] px-4 py-8">
      <div className="mx-auto max-w-2xl">
        <header className="mb-6 flex items-center gap-3">
          <img src={skybreIcon} alt="Skybre" className="h-10 w-10 shrink-0 object-contain" />
          <div>
            <p className="text-sm font-semibold text-[var(--text-primary)]">Skybre</p>
            <h1 className="text-lg font-semibold text-[var(--text-primary)]">
              {data.customer_name} — data usage
            </h1>
            <p className="text-sm text-[var(--text-muted)]">
              {MONTHS[data.period.month - 1]} {data.period.year}
            </p>
          </div>
        </header>

        {/* Live status first -- it's what someone opening this link at 8pm
            actually wants to know. */}
        <section className="mb-4 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
          <div className="mb-3 flex items-center gap-2">
            <span
              className={`inline-block h-2.5 w-2.5 rounded-full ${online ? "bg-[#0ca30c]" : "bg-[var(--text-muted)]"}`}
            />
            <span className="text-sm font-medium text-[var(--text-primary)]">
              {online ? "Connected" : "Not connected"}
            </span>
          </div>

          {online ? (
            data.live_sessions.map((s, i) => {
              const rate = formatBpsPair(s.download_bps, s.upload_bps);
              return (
              <div key={i} className="grid grid-cols-2 gap-4 border-t border-[var(--border-hairline)] pt-3 first:border-0 first:pt-0">
                <div>
                  <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Download now</p>
                  <p className="text-2xl font-semibold tabular-nums text-[var(--text-primary)]">
                    {rate.down}
                  </p>
                </div>
                <div>
                  <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Upload now</p>
                  <p className="text-2xl font-semibold tabular-nums text-[var(--text-primary)]">
                    {rate.up}
                  </p>
                </div>
                <div className="col-span-2 text-xs text-[var(--text-muted)]">
                  This session: {formatBytes(s.download_bytes)} down, {formatBytes(s.upload_bytes)} up
                  {s.rate_measured_at ? null : " · speed not measured yet"}
                </div>
              </div>
              );
            })
          ) : (
            <p className="text-sm text-[var(--text-secondary)]">
              We're not seeing a connection right now. If your internet is working, this page may just
              be a few minutes behind.
            </p>
          )}
        </section>

        <section className="mb-4 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
          <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Used this month</p>
          <p className="mt-1 text-3xl font-semibold tabular-nums text-[var(--text-primary)]">
            {formatBytes(data.total_bytes)}
          </p>

          {capPct != null && (
            <div className="mt-4">
              <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--tint-hover)]">
                <div
                  className="h-full rounded-full bg-[var(--series-1)]"
                  style={{ width: `${capPct}%` }}
                />
              </div>
              <p className="mt-1.5 text-xs text-[var(--text-muted)]">
                {capPct}% of {formatBytes(data.cap_bytes)}
              </p>
            </div>
          )}

          <div className="mt-4 grid grid-cols-2 gap-4 border-t border-[var(--border-hairline)] pt-3">
            <div>
              <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Downloaded</p>
              <p className="text-lg font-medium tabular-nums text-[var(--text-primary)]">
                {formatBytes(data.download_bytes)}
              </p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Uploaded</p>
              <p className="text-lg font-medium tabular-nums text-[var(--text-primary)]">
                {formatBytes(data.upload_bytes)}
              </p>
            </div>
          </div>
        </section>

        {/* Their own history, same chart staff see. The whole point of this
            page is that a customer can answer "how much have I used" without
            phoning in -- "this month so far" only half answers it. */}
        <section className="mb-4 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">Your usage over time</h2>
            <PeriodSwitcher value={period} onChange={setPeriod} />
          </div>
          <UsageChart series={data.series ?? null} measuringSince={data.measuring_since} />
        </section>

        <p className="text-center text-xs text-[var(--text-muted)]">
          Updates automatically. Monthly totals come from your connection's own reporting and can lag a
          few minutes behind.
        </p>
      </div>
    </div>
  );
}
