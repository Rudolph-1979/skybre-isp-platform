import { useMemo } from "react";
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

/**
 * Data usage over a chosen period, as a stacked bar per interval.
 *
 * Form: the question is "how much, over time, in discrete intervals", which is
 * magnitude over an ordinal axis -- bars, not a line. Download and upload are
 * STACKED rather than drawn side by side because the headline is the total a
 * customer consumed (that is what a cap is measured against); the split is the
 * secondary read, which stacking still gives.
 *
 * Colour comes from --chart-1 / --chart-2, which are separate tokens from the
 * --series-* accents used for text. Both modes were run through the dataviz
 * validator: lightness band, chroma floor, colourblind separation, normal-vision
 * separation and contrast all pass, in light and dark independently.
 *
 * Two series, so a legend is always present, and each segment is also named in
 * the tooltip -- identity is never carried by colour alone.
 */

export type UsagePoint = {
  at: string;
  label: string;
  download_bytes: number;
  upload_bytes: number;
  total_bytes: number;
};

export type UsageSeries = {
  period: Period;
  period_label: string;
  interval: "hour" | "day" | "month";
  download_bytes: number;
  upload_bytes: number;
  total_bytes: number;
  points: UsagePoint[];
};

export type Period = "day" | "week" | "month" | "year";

const PERIODS: { key: Period; label: string }[] = [
  { key: "day", label: "Day" },
  { key: "week", label: "Week" },
  { key: "month", label: "Month" },
  { key: "year", label: "Year" },
];

/** Binary units, because that is what a data cap is sold in. */
export function formatBytes(bytes: number) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  // One decimal below 100 so 6.5 GB doesn't read as 6 GB; none above, where it
  // is noise.
  return `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

function Swatch({ token, children }: { token: string; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
      <span
        aria-hidden="true"
        className="inline-block h-2.5 w-2.5 rounded-sm"
        style={{ backgroundColor: `var(${token})` }}
      />
      {children}
    </span>
  );
}

function UsageTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  const point: UsagePoint = payload[0].payload;
  return (
    <div className="rounded-md border border-[var(--baseline)] bg-[var(--surface-1)] px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-medium text-[var(--text-primary)]">{label}</p>
      {/* Named, not just coloured -- the swatch is the secondary cue. */}
      <p className="flex items-center justify-between gap-4">
        <Swatch token="--chart-1">Download</Swatch>
        <span className="tabular-nums text-[var(--text-primary)]">{formatBytes(point.download_bytes)}</span>
      </p>
      <p className="flex items-center justify-between gap-4">
        <Swatch token="--chart-2">Upload</Swatch>
        <span className="tabular-nums text-[var(--text-primary)]">{formatBytes(point.upload_bytes)}</span>
      </p>
      <p className="mt-1 flex items-center justify-between gap-4 border-t border-[var(--border-hairline)] pt-1">
        <span className="text-[var(--text-secondary)]">Total</span>
        <span className="font-medium tabular-nums text-[var(--text-primary)]">
          {formatBytes(point.total_bytes)}
        </span>
      </p>
    </div>
  );
}

export function PeriodSwitcher({
  value,
  onChange,
  className = "",
}: {
  value: Period;
  onChange: (p: Period) => void;
  className?: string;
}) {
  return (
    <div className={`inline-flex rounded-md border border-[var(--baseline)] p-0.5 ${className}`}>
      {PERIODS.map((p) => (
        <button
          key={p.key}
          type="button"
          aria-pressed={value === p.key}
          className={`rounded px-2.5 py-1 text-xs ${
            value === p.key
              ? "bg-[var(--tint-hover)] font-medium text-[var(--text-primary)]"
              : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
          }`}
          onClick={() => onChange(p.key)}
        >
          {p.label}
        </button>
      ))}
    </div>
  );
}

export function UsageChart({
  series,
  measuringSince,
  height = 220,
}: {
  series: UsageSeries | null;
  /** When accumulation began. An empty year is otherwise indistinguishable
   *  from a customer who used nothing. */
  measuringSince?: string | null;
  height?: number;
}) {
  const empty = !series || series.total_bytes === 0;

  const sinceNote = useMemo(() => {
    if (!measuringSince || !series) return null;
    const since = new Date(measuringSince);
    if (Number.isNaN(since.getTime())) return null;
    // Only worth saying when the record actually starts inside the window
    // being looked at -- otherwise it explains nothing.
    if (since <= new Date(series.points[0]?.at ?? 0)) return null;
    return since.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  }, [measuringSince, series]);

  if (!series) return <p className="text-sm text-[var(--text-muted)]">Loading…</p>;

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <p className="text-2xl font-semibold tabular-nums text-[var(--text-primary)]">
            {formatBytes(series.total_bytes)}
          </p>
          <p className="text-xs text-[var(--text-muted)]">
            {formatBytes(series.download_bytes)} down · {formatBytes(series.upload_bytes)} up ·{" "}
            {series.period_label}
          </p>
        </div>
        {/* Legend, always present for two series. */}
        <div className="flex gap-3">
          <Swatch token="--chart-1">Download</Swatch>
          <Swatch token="--chart-2">Upload</Swatch>
        </div>
      </div>

      {empty ? (
        <p className="py-8 text-center text-sm text-[var(--text-muted)]">
          No usage recorded for {series.period_label}.
        </p>
      ) : (
        <ResponsiveContainer width="100%" height={height}>
          <BarChart data={series.points} margin={{ top: 12, right: 8, left: 0, bottom: 0 }} barCategoryGap="28%">
            <CartesianGrid stroke="var(--border-hairline)" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 11, fill: "var(--text-muted)" }}
              axisLine={{ stroke: "var(--border-hairline)" }}
              tickLine={false}
              // Let Recharts drop labels that would collide, rather than
              // hardcoding "every other". This chart renders at three
              // different widths -- the customer card, the customer's own
              // page, and a phone -- so a fixed interval that fits one of
              // them overlaps in another. 24 hourly labels at card width ran
              // together into "00:0001:0002:00" before this.
              interval="preserveStartEnd"
              minTickGap={series.interval === "hour" ? 28 : 18}
            />
            <YAxis
              tick={{ fontSize: 11, fill: "var(--text-muted)" }}
              axisLine={false}
              tickLine={false}
              // Wide enough for "362 MB" on one line; at 52 it wrapped.
              width={62}
              tickFormatter={(v) => formatBytes(Number(v))}
            />
            <Tooltip
              content={<UsageTooltip />}
              cursor={{ fill: "var(--border-hairline)", fillOpacity: 0.35 }}
            />
            {/* Stacked: total height is the figure a cap is measured against.
                The 2px surface-coloured stroke is the gap the dataviz spec
                asks for between abutting fills -- without it the two segments
                read as one block at small heights. Only the TOP of the stack
                is rounded, so the corner marks the data end rather than
                appearing mid-column. */}
            {/* isAnimationActive={false}: the chart re-renders on every
                period switch, and a grow-from-zero animation on each one is
                noise rather than information. It also means the bars are
                present on first paint, which matters for anything rendering
                the page without a running animation loop. */}
            <Bar dataKey="download_bytes" stackId="u" fill="var(--chart-1)" maxBarSize={26}
                 stroke="var(--surface-1)" strokeWidth={2} isAnimationActive={false} />
            <Bar dataKey="upload_bytes" stackId="u" fill="var(--chart-2)" maxBarSize={26}
                 stroke="var(--surface-1)" strokeWidth={2} radius={[4, 4, 0, 0]}
                 isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      )}

      {sinceNote && (
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          Usage has only been recorded since {sinceNote}; anything before that is blank because it
          was never measured, not because it was zero.
        </p>
      )}
    </div>
  );
}
