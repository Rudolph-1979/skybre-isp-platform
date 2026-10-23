import { useEffect, useMemo, useState } from "react";
import {
  Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../api/client";

/**
 * One UISP device's graphs (#170, Rudolph 14 Oct 2026: "Lets build the
 * graphs"). Read from UISP's own history: Hour / Day / Week / Month.
 * Throughput is the radio's traffic with UISP's downlink = AP -> station.
 * UISP keeps no latency for these radios, so there is no latency graph.
 */

export type UispGraphDevice = {
  id: string;
  name: string;
  model: string;
  kind: string;
  black_box: boolean;
  online: boolean;
  ip: string;
  site: { id: string; name: string } | null;
};

type Interval = "hour" | "day" | "week" | "month";
type Point = { t: number } & Record<string, number>;
type Stats = {
  detail?: string;
  interval: Interval;
  period_s: number;
  interface: string;
  wireless_mode: string;
  series: string[];
  points: Point[];
};

const RANGES: { key: Interval; label: string }[] = [
  { key: "hour", label: "Hour" },
  { key: "day", label: "Day" },
  { key: "week", label: "Week" },
  { key: "month", label: "Month" },
];

type Series = {
  key: string;
  label: string;
  color: string;
  area?: boolean;
  dashed?: boolean;
  faint?: boolean;
  format?: (v: number) => string;
};
type ChartDef = {
  title: string;
  hint?: string;
  series: Series[];
  format: (v: number) => string;
  axis: (v: number) => string;
  domain?: [number | string | ((v: number) => number), number | string | ((v: number) => number)];
  refs?: { y: number; label: string }[];
  apOnly?: boolean;
  yStep?: number;
};

export function bps(v: number) {
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)} Gbps`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)} Mbps`;
  if (v >= 1e3) return `${Math.round(v / 1e3)} kbps`;
  return `${Math.round(v)} bps`;
}
function axisBps(v: number) {
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}G`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(v < 1e7 && v % 1e6 ? 1 : 0)}M`;
  if (v >= 1e3) return `${Math.round(v / 1e3)}k`;
  return `${v}`;
}
const pct = (v: number) => `${Math.round(v)}%`;
const dbm = (v: number) => `${Math.round(v)} dBm`;

const CHARTS: ChartDef[] = [
  {
    title: "Throughput",
    hint: "Traffic over the radio. Dashed: what the link can carry.",
    series: [
      { key: "down_bps", label: "Downlink", color: "var(--chart-1)", area: true },
      { key: "up_bps", label: "Uplink", color: "var(--chart-2)" },
      { key: "downlinkCapacity", label: "Downlink capacity", color: "var(--chart-1)", dashed: true },
      { key: "uplinkCapacity", label: "Uplink capacity", color: "var(--chart-2)", dashed: true },
    ],
    format: bps,
    axis: axisBps,
  },
  {
    title: "Link use",
    hint: "Share of capacity in use. Flagged busy at 60%.",
    series: [
      { key: "downlinkUtilization", label: "Downlink", color: "var(--chart-1)", area: true },
      { key: "uplinkUtilization", label: "Uplink", color: "var(--chart-2)" },
    ],
    format: pct,
    axis: pct,
    domain: [0, 100],
    refs: [{ y: 60, label: "busy" }],
  },
  {
    title: "Signal",
    hint: "Local and remote; thin lines are the separate chains.",
    series: [
      { key: "signal", label: "Local", color: "var(--series-3)" },
      { key: "remoteSignal", label: "Remote", color: "var(--series-7)" },
      { key: "localChain0", label: "Local ch 0", color: "var(--series-3)", faint: true },
      { key: "localChain1", label: "Local ch 1", color: "var(--series-3)", faint: true },
      { key: "remoteChain0", label: "Remote ch 0", color: "var(--series-7)", faint: true },
      { key: "remoteChain1", label: "Remote ch 1", color: "var(--series-7)", faint: true },
    ],
    format: dbm,
    axis: (v) => `${v}`,
    yStep: 5,
  },
  {
    title: "Link potential",
    hint: "UISP's link score. Flagged low under 50%.",
    series: [
      { key: "linkScore", label: "Link", color: "var(--series-6)", area: true },
      { key: "downlinkScore", label: "Downlink", color: "var(--chart-1)" },
      { key: "uplinkScore", label: "Uplink", color: "var(--chart-2)" },
    ],
    format: pct,
    axis: pct,
    domain: [0, 100],
    refs: [{ y: 50, label: "low" }],
  },
  {
    title: "Stations & airtime",
    hint: "Stations connected to this AP, and how busy the air is.",
    series: [
      { key: "clients", label: "Stations", color: "var(--series-4)", format: (v) => `${Math.round(v * 10) / 10}` },
      { key: "airTime", label: "Airtime", color: "var(--series-5)", format: (v) => `${Math.round(v * 10) / 10}%` },
    ],
    format: (v) => `${Math.round(v * 10) / 10}`,
    axis: (v) => `${v}`,
    apOnly: true,
  },
  {
    title: "CPU & RAM",
    series: [
      { key: "cpu", label: "CPU", color: "var(--series-8)" },
      { key: "ram", label: "RAM", color: "var(--series-4)" },
    ],
    format: pct,
    axis: pct,
    domain: [0, 100],
  },
];

function tick(t: number, interval: Interval) {
  const d = new Date(t);
  if (interval === "hour" || interval === "day") return d.toLocaleTimeString("en-ZA", { hour: "2-digit", minute: "2-digit" });
  if (interval === "week") return d.toLocaleDateString("en-ZA", { weekday: "short", day: "2-digit" });
  return d.toLocaleDateString("en-ZA", { day: "2-digit", month: "short" });
}
/** Round-time ticks: 10 min / 3 h / midnight / every 5th midnight. */
function ticks(points: Point[], interval: Interval) {
  if (points.length < 2) return undefined;
  const min = points[0].t;
  const max = points[points.length - 1].t;
  const d = new Date(min);
  d.setSeconds(0, 0);
  let step: number;
  if (interval === "hour") {
    d.setMinutes(Math.ceil(d.getMinutes() / 10) * 10);
    step = 10 * 60_000;
  } else if (interval === "day") {
    d.setMinutes(0);
    d.setHours(Math.ceil((d.getHours() + (min > d.getTime() ? 1 : 0)) / 3) * 3);
    step = 3 * 3_600_000;
  } else {
    d.setHours(24, 0, 0, 0);
    step = (interval === "week" ? 1 : 5) * 86_400_000;
  }
  const out: number[] = [];
  for (let t = d.getTime(); t <= max; t += step) if (t >= min) out.push(t);
  return out;
}

function when(t: number) {
  return new Date(t).toLocaleString("en-ZA", { weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

function stats(points: Point[], key: string) {
  const vals = points.map((p) => p[key]).filter((v): v is number => typeof v === "number");
  if (!vals.length) return null;
  return {
    max: Math.max(...vals),
    avg: vals.reduce((a, b) => a + b, 0) / vals.length,
    last: vals[vals.length - 1],
  };
}

function GraphTooltip({ active, payload, chart }: any) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload as Point;
  const def = chart as ChartDef;
  return (
    <div className="rounded-md border border-[var(--baseline)] bg-[var(--surface-1)] px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-medium text-[var(--text-primary)]">{when(p.t)}</p>
      {def.series
        .filter((s) => typeof p[s.key] === "number")
        .map((s) => (
          <p key={s.key} className="flex justify-between gap-4">
            <span style={{ color: s.color }}>{s.label}</span>
            <span className="tabular-nums text-[var(--text-primary)]">{(s.format ?? def.format)(p[s.key])}</span>
          </p>
        ))}
    </div>
  );
}

function Graph({ def, points, present, interval }: { def: ChartDef; points: Point[]; present: Set<string>; interval: Interval }) {
  // Capacity lines (dashed) squash light traffic flat against the floor, so
  // they start hidden when traffic peaks under 10% of capacity.
  const capacityUseful = useMemo(() => {
    const traffic = Math.max(0, ...points.map((p) => Math.max(p.down_bps ?? 0, p.up_bps ?? 0)));
    const cap = Math.max(0, ...points.map((p) => Math.max(p.downlinkCapacity ?? 0, p.uplinkCapacity ?? 0)));
    return !cap || traffic >= cap * 0.1;
  }, [points]);
  const [showDashed, setShowDashed] = useState<boolean | null>(null);
  const dashedOn = showDashed ?? capacityUseful;
  const hasDashed = def.series.some((s) => s.dashed && present.has(s.key));
  const series = def.series.filter((s) => present.has(s.key) && (!s.dashed || dashedOn));
  // Even steps (signal: every 5 dBm) instead of recharts' odd ones.
  const yTicks = useMemo(() => {
    if (!def.yStep) return undefined;
    const vals = points.flatMap((p) => series.map((s) => p[s.key])).filter((v): v is number => typeof v === "number");
    if (!vals.length) return undefined;
    const step = def.yStep;
    const lo = Math.floor((Math.min(...vals) - 1) / step) * step;
    const hi = Math.ceil((Math.max(...vals) + 1) / step) * step;
    const out: number[] = [];
    for (let v = lo; v <= hi; v += step) out.push(v);
    return out;
  }, [def.yStep, points, series]);
  const main = series.filter((s) => !s.faint && !s.dashed);
  return (
    <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-3">
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-semibold text-[var(--text-primary)]">{def.title}</p>
        <div className="flex flex-wrap gap-3 text-[11px] text-[var(--text-secondary)]">
          {series.filter((s) => !s.faint).map((s) => (
            <span key={s.key} className="inline-flex items-center gap-1.5">
              <span
                className={s.dashed ? "inline-block w-3 border-t-2 border-dashed" : "inline-block h-2 w-3 rounded-sm"}
                style={s.dashed ? { borderColor: s.color } : { background: s.color }}
              />
              {s.label}
            </span>
          ))}
        </div>
      </div>
      {(def.hint || hasDashed) && (
        <p className="mb-1 flex flex-wrap items-center gap-2 text-[11px] text-[var(--text-muted)]">
          {def.hint}
          {hasDashed && (
            <button type="button" className="rounded border border-[var(--baseline)] px-1.5 py-0.5 hover:text-[var(--text-primary)]" onClick={() => setShowDashed(!dashedOn)}>
              {dashedOn ? "Hide capacity" : "Show capacity"}
            </button>
          )}
        </p>
      )}
      <ResponsiveContainer width="100%" height={180}>
        <ComposedChart data={points} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--border-hairline)" vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            ticks={ticks(points, interval)}
            tickFormatter={(t) => tick(Number(t), interval)}
            tick={{ fontSize: 10, fill: "var(--text-muted)" }}
            axisLine={{ stroke: "var(--border-hairline)" }}
            tickLine={false}
            minTickGap={24}
          />
          <YAxis
            width={46}
            domain={yTicks ? [yTicks[0], yTicks[yTicks.length - 1]] : def.domain ?? [0, "auto"]}
            ticks={yTicks}
            tickFormatter={(v) => def.axis(Number(v))}
            tick={{ fontSize: 10, fill: "var(--text-muted)" }}
            axisLine={false}
            tickLine={false}
            allowDecimals={false}
          />
          <Tooltip content={<GraphTooltip chart={def} />} />
          {(def.refs ?? []).map((r) => (
            <ReferenceLine key={r.y} y={r.y} stroke="var(--status-warning)" strokeDasharray="4 3" label={{ value: r.label, position: "insideTopRight", fontSize: 10, fill: "var(--status-warning)" }} />
          ))}
          {series.map((s) =>
            s.area ? (
              <Area key={s.key} type="monotone" dataKey={s.key} stroke={s.color} fill={s.color} fillOpacity={0.25} strokeWidth={1.5} dot={false} isAnimationActive={false} connectNulls />
            ) : (
              <Line
                key={s.key}
                type="monotone"
                dataKey={s.key}
                stroke={s.color}
                strokeWidth={s.faint ? 1 : 1.5}
                strokeOpacity={s.faint ? 0.35 : 1}
                strokeDasharray={s.dashed ? "5 4" : undefined}
                dot={false}
                isAnimationActive={false}
                connectNulls
              />
            ),
          )}
        </ComposedChart>
      </ResponsiveContainer>
      <div className="mt-1 grid gap-x-4 text-[11px] text-[var(--text-secondary)]" style={{ gridTemplateColumns: "auto repeat(3, minmax(0, 1fr))" }}>
        <span />
        <span className="text-[var(--text-muted)]">Max</span>
        <span className="text-[var(--text-muted)]">Average</span>
        <span className="text-[var(--text-muted)]">Last</span>
        {main.map((s) => {
          const st = stats(points, s.key);
          if (!st) return null;
          const f = s.format ?? def.format;
          return (
            <span key={s.key} className="contents">
              <span style={{ color: s.color }}>{s.label}</span>
              <span className="tabular-nums">{f(st.max)}</span>
              <span className="tabular-nums">{f(st.avg)}</span>
              <span className="tabular-nums">{f(st.last)}</span>
            </span>
          );
        })}
      </div>
    </div>
  );
}

export function UispDeviceGraphs({ device, onClose }: { device: UispGraphDevice; onClose: () => void }) {
  const [interval, setInterval_] = useState<Interval>("day");
  const [data, setData] = useState<Stats | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (device.black_box) return;
    let cancelled = false;
    function load() {
      setLoading(true);
      api
        .get<Stats>(`/uisp/devices/${device.id}/statistics/?interval=${interval}`)
        .then((res) => {
          if (cancelled) return;
          setData(res.data);
          setError("");
        })
        .catch((err) => {
          if (!cancelled) setError(err?.response?.data?.detail ?? "Couldn't read this device's history from UISP.");
        })
        .finally(() => !cancelled && setLoading(false));
    }
    load();
    const timer = window.setInterval(() => {
      if (!document.hidden && (interval === "hour" || interval === "day")) load();
    }, 60_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [device.id, device.black_box, interval]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const present = useMemo(() => new Set(data?.series ?? []), [data]);
  const step = useMemo(() => {
    const pts = data?.points ?? [];
    if (pts.length < 2) return "";
    const min = Math.round((pts[1].t - pts[0].t) / 60000);
    return min >= 60 && min % 60 === 0 ? `${min / 60} h` : `${min} min`;
  }, [data]);
  const charts = CHARTS.filter(
    (c) => (!c.apOnly || device.kind === "ap") && c.series.some((s) => !s.dashed && !s.faint && present.has(s.key)),
  );

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-2 sm:p-6" onClick={onClose}>
      <div className="w-full max-w-6xl rounded-lg bg-[var(--surface-0,var(--surface-1))] p-4 shadow-xl sm:p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-[var(--text-primary)]">{device.name}</h2>
            <p className="text-xs text-[var(--text-muted)]">
              {device.model}
              {device.site ? ` · ${device.site.name}` : ""}
              {device.ip ? ` · ${device.ip}` : ""}
              {data?.interface ? ` · radio ${data.interface}` : ""}
              {!device.online && <span className="ml-1 text-[var(--status-critical)]">· offline</span>}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {!device.black_box && (
              <div className="inline-flex rounded-md border border-[var(--baseline)] p-0.5">
                {RANGES.map((r) => (
                  <button
                    key={r.key}
                    type="button"
                    aria-pressed={interval === r.key}
                    className={`rounded px-2.5 py-1 text-xs ${
                      interval === r.key
                        ? "bg-[var(--tint-hover)] font-medium text-[var(--text-primary)]"
                        : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
                    }`}
                    onClick={() => setInterval_(r.key)}
                  >
                    {r.label}
                  </button>
                ))}
              </div>
            )}
            <button type="button" className="rounded-md px-2 py-1 text-sm text-[var(--text-muted)] hover:bg-[var(--tint-hover)]" onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
        </div>

        {device.black_box ? (
          <p className="text-sm text-[var(--text-secondary)]">
            UISP treats this device as a "black box": it only knows whether it answers, so there is no history to graph.
          </p>
        ) : (
          <>
            {error && <p className="mb-2 text-sm text-[var(--status-critical)]">{error}</p>}
            {!data && !error && <p className="text-sm text-[var(--text-muted)]">Reading UISP…</p>}
            {data && data.interval === interval && charts.length === 0 && (
              <p className="text-sm text-[var(--text-muted)]">UISP has no history for this device in this range.</p>
            )}
            {data && (
              <div className={`grid gap-3 md:grid-cols-2 ${loading ? "opacity-60" : ""}`}>
                {charts.map((c) => (
                  <Graph key={c.title} def={c} points={data.points} present={present} interval={data.interval} />
                ))}
              </div>
            )}
            <p className="mt-3 text-[11px] text-[var(--text-muted)]">
              From UISP's history{step ? `, one point per ${step}` : ""}. Downlink is the AP → station
              direction of the link. UISP keeps no latency for these radios.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
