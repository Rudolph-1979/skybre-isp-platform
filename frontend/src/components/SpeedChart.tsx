import { useEffect, useMemo, useState } from "react";
import {
  Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../api/client";

/**
 * Speed over time, Splynx/MRTG style (Rudolph, 5 Oct 2026: "This shows very
 * nicely when customer over utilised his line").
 *
 * Each point is the average speed over its interval (5 min / 30 min / 2 h /
 * 1 day for the four ranges), from radiusauth.speed on the backend. Download
 * is a filled area, upload a line, and the plan speed a dashed line -- a
 * download area flattening against that line is a customer using everything
 * they pay for. Under it, Maximum / Average / Last like Splynx's footer.
 */

type SpeedRange = "day" | "week" | "month" | "year";
type Rate = { download_bps: number; upload_bps: number };
type SpeedPoint = Rate & { at: string };
type SpeedLine = { username: string; label: string; download_bps: number; upload_bps: number };
type SpeedData = {
  range: SpeedRange;
  interval_seconds: number;
  points: SpeedPoint[];
  max: Rate;
  avg: Rate;
  last: Rate;
  plan: Rate | null;
  detail_since: string | null;
  lines: SpeedLine[];
  line: string | null;
};

const RANGES: { key: SpeedRange; label: string }[] = [
  { key: "day", label: "24 hours" },
  { key: "week", label: "7 days" },
  { key: "month", label: "31 days" },
  { key: "year", label: "12 months" },
];

const REFRESH_MS = 60_000;

export function formatBps(bps: number) {
  if (bps >= 1_000_000) return `${(bps / 1_000_000).toFixed(2)} Mbps`;
  if (bps >= 1_000) return `${Math.round(bps / 1_000)} kbps`;
  return `${bps} bps`;
}

function axisBps(bps: number) {
  if (bps >= 1_000_000) return `${(bps / 1_000_000).toFixed(bps >= 10_000_000 ? 0 : 1)} M`;
  if (bps >= 1_000) return `${Math.round(bps / 1_000)} k`;
  return `${bps}`;
}

function tickLabel(t: number, range: SpeedRange) {
  const d = new Date(t);
  if (range === "day") return d.toLocaleTimeString("en-ZA", { hour: "2-digit", minute: "2-digit" });
  if (range === "week") return d.toLocaleDateString("en-ZA", { weekday: "short", day: "2-digit" });
  if (range === "month") return d.toLocaleDateString("en-ZA", { day: "2-digit", month: "short" });
  return d.toLocaleDateString("en-ZA", { month: "short", year: "2-digit" });
}

function pointLabel(t: number, range: SpeedRange) {
  const d = new Date(t);
  if (range === "year") return d.toLocaleDateString("en-ZA", { weekday: "short", day: "2-digit", month: "short", year: "numeric" });
  return d.toLocaleString("en-ZA", { weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

function SpeedTooltip({ active, payload, range }: any) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload as SpeedPoint & { t: number };
  return (
    <div className="rounded-md border border-[var(--baseline)] bg-[var(--surface-1)] px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-medium text-[var(--text-primary)]">{pointLabel(p.t, range)}</p>
      <p className="flex justify-between gap-4">
        <span style={{ color: "var(--chart-1)" }}>↓ Download</span>
        <span className="tabular-nums text-[var(--text-primary)]">{formatBps(p.download_bps)}</span>
      </p>
      <p className="flex justify-between gap-4">
        <span style={{ color: "var(--chart-2)" }}>↑ Upload</span>
        <span className="tabular-nums text-[var(--text-primary)]">{formatBps(p.upload_bps)}</span>
      </p>
    </div>
  );
}

export function SpeedChart({ customerId, height = 240 }: { customerId: number; height?: number }) {
  const [range, setRange] = useState<SpeedRange>("day");
  const [line, setLine] = useState("");
  const [data, setData] = useState<SpeedData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    function load() {
      const q = `range=${range}${line ? `&line=${encodeURIComponent(line)}` : ""}`;
      api
        .get<SpeedData>(`/customers/${customerId}/speed/?${q}`)
        .then((res) => {
          if (!cancelled) {
            setData(res.data);
            setError("");
          }
        })
        .catch(() => {
          if (!cancelled) setError("Couldn't load the speed graph.");
        });
    }
    load();
    const timer = setInterval(() => {
      if (!document.hidden) load();
    }, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [customerId, range, line]);

  const points = useMemo(
    () => (data?.points ?? []).map((p) => ({ ...p, t: new Date(p.at).getTime() })),
    [data],
  );

  // How long the line sat at (90%+ of) its plan download speed in this
  // window -- the over-utilisation the graph shows, as a number.
  const atPlan = useMemo(() => {
    if (!data?.plan?.download_bps || !points.length) return null;
    const near = points.filter((p) => p.download_bps >= data.plan!.download_bps * 0.9).length;
    const seconds = near * data.interval_seconds;
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.round((seconds % 3600) / 60);
    return {
      text: hours ? `${hours}h ${minutes}m` : `${minutes}m`,
      pct: Math.round((near / points.length) * 100),
    };
  }, [data, points]);

  const yMax = useMemo(() => {
    const top = Math.max(0, ...points.map((p) => Math.max(p.download_bps, p.upload_bps)));
    return Math.max(top, (data?.plan?.download_bps ?? 0) * 1.1, 1000);
  }, [points, data]);

  const detailNote = useMemo(() => {
    if (!data?.detail_since || (data.range !== "day" && data.range !== "week") || !points.length) return null;
    const since = new Date(data.detail_since);
    if (since.getTime() <= points[0].t) return null;
    return since.toLocaleString("en-ZA", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  }, [data, points]);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-semibold text-[var(--text-primary)]">Speed over time</p>
        <div className="flex flex-wrap items-center gap-2">
          {data && data.lines.length > 1 && (
            <select
              className="rounded-md border border-[var(--baseline)] bg-[var(--surface-1)] px-2 py-1 text-xs text-[var(--text-primary)]"
              value={line}
              onChange={(e) => setLine(e.target.value)}
            >
              <option value="">All lines</option>
              {data.lines.map((l) => (
                <option key={l.username} value={l.username}>{l.label}</option>
              ))}
            </select>
          )}
          <div className="inline-flex rounded-md border border-[var(--baseline)] p-0.5">
            {RANGES.map((r) => (
              <button
                key={r.key}
                type="button"
                aria-pressed={range === r.key}
                className={`rounded px-2.5 py-1 text-xs ${
                  range === r.key
                    ? "bg-[var(--tint-hover)] font-medium text-[var(--text-primary)]"
                    : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
                }`}
                onClick={() => setRange(r.key)}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {error && <p className="text-sm text-[var(--text-muted)]">{error}</p>}
      {!data && !error && <p className="text-sm text-[var(--text-muted)]">Loading…</p>}

      {data && (
        <>
          <div className="mb-1 flex flex-wrap justify-end gap-3 text-xs text-[var(--text-secondary)]">
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: "var(--chart-1)" }} />
              Download
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block h-0.5 w-3" style={{ background: "var(--chart-2)" }} />
              Upload
            </span>
            {data.plan && (
              <span className="inline-flex items-center gap-1.5">
                <span className="inline-block w-3 border-t-2 border-dashed" style={{ borderColor: "var(--status-critical)" }} />
                Plan {formatBps(data.plan.download_bps)} down
              </span>
            )}
          </div>
          <ResponsiveContainer width="100%" height={height}>
            <ComposedChart data={points} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--border-hairline)" vertical={false} />
              <XAxis
                dataKey="t"
                type="number"
                scale="time"
                domain={["dataMin", "dataMax"]}
                tickFormatter={(t) => tickLabel(Number(t), data.range)}
                tick={{ fontSize: 11, fill: "var(--text-muted)" }}
                axisLine={{ stroke: "var(--border-hairline)" }}
                tickLine={false}
                minTickGap={40}
              />
              <YAxis
                domain={[0, yMax]}
                tickFormatter={(v) => axisBps(Number(v))}
                tick={{ fontSize: 11, fill: "var(--text-muted)" }}
                axisLine={false}
                tickLine={false}
                width={48}
              />
              <Tooltip content={<SpeedTooltip range={data.range} />} />
              <Area
                type="stepAfter"
                dataKey="download_bps"
                stroke="var(--chart-1)"
                fill="var(--chart-1)"
                fillOpacity={0.45}
                strokeWidth={1}
                isAnimationActive={false}
              />
              <Line
                type="stepAfter"
                dataKey="upload_bps"
                stroke="var(--chart-2)"
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
              />
              {data.plan && (
                <ReferenceLine y={data.plan.download_bps} stroke="var(--status-critical)" strokeDasharray="5 4" />
              )}
            </ComposedChart>
          </ResponsiveContainer>

          <div className="mt-2 overflow-x-auto">
            <table className="text-xs tabular-nums">
              <thead>
                <tr className="text-[var(--text-muted)]">
                  <th className="pr-6 text-left font-normal"></th>
                  <th className="pr-6 text-right font-normal">Maximum</th>
                  <th className="pr-6 text-right font-normal">Average</th>
                  <th className="pr-6 text-right font-normal">Last</th>
                  {data.plan && <th className="text-right font-normal">Plan</th>}
                </tr>
              </thead>
              <tbody className="text-[var(--text-primary)]">
                <tr>
                  <td className="pr-6" style={{ color: "var(--chart-1)" }}>↓ Download</td>
                  <td className="pr-6 text-right">{formatBps(data.max.download_bps)}</td>
                  <td className="pr-6 text-right">{formatBps(data.avg.download_bps)}</td>
                  <td className="pr-6 text-right">{formatBps(data.last.download_bps)}</td>
                  {data.plan && <td className="text-right">{formatBps(data.plan.download_bps)}</td>}
                </tr>
                <tr>
                  <td className="pr-6" style={{ color: "var(--chart-2)" }}>↑ Upload</td>
                  <td className="pr-6 text-right">{formatBps(data.max.upload_bps)}</td>
                  <td className="pr-6 text-right">{formatBps(data.avg.upload_bps)}</td>
                  <td className="pr-6 text-right">{formatBps(data.last.upload_bps)}</td>
                  {data.plan && <td className="text-right">{formatBps(data.plan.upload_bps)}</td>}
                </tr>
              </tbody>
            </table>
          </div>
          {atPlan && (
            <p className="mt-2 text-xs text-[var(--text-secondary)]">
              At 90%+ of plan download speed for <span className="font-medium">{atPlan.text}</span> ({atPlan.pct}% of
              this window).
            </p>
          )}
          {detailNote && (
            <p className="mt-1 text-xs text-[var(--text-muted)]">
              5-minute detail since {detailNote}; before that each point shows its hour's average.
            </p>
          )}
        </>
      )}
    </div>
  );
}
