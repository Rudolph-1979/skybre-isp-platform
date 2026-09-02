import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { PeriodSwitcher, formatBytes, type Period } from "../../components/UsageChart";
import type { UsageReport } from "../../types";

/**
 * Who is loading the network, and who is near a cap.
 *
 * A table rather than a chart, deliberately: the question is "which customers,
 * and how much each" -- names and figures answer that directly, where forty
 * bars labelled with customer names would be the same numbers, harder to read
 * and impossible to sort. The one visual element is the cap bar, which is a
 * part-to-whole for a single value and reads faster than a percentage alone.
 */

function capTone(pct: number | null) {
  if (pct == null) return null;
  // Status colours, used for status and reserved for it -- and always with the
  // number beside them, never colour alone.
  if (pct >= 100) return "var(--status-critical)";
  if (pct >= 90) return "var(--status-serious)";
  if (pct >= 75) return "var(--status-warning)";
  return "var(--chart-1)";
}

export function UsageReportPage() {
  const [period, setPeriod] = useState<Period>("month");
  const [report, setReport] = useState<UsageReport | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .get<UsageReport>(`/usage-report/?period=${period}`)
      .then((res) => {
        if (cancelled) return;
        setReport(res.data);
        setError("");
      })
      .catch(() => {
        if (!cancelled) setError("Couldn't load the usage report.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [period]);

  const rows = report?.results ?? [];
  const grandTotal = rows.reduce((sum, r) => sum + r.total_bytes, 0);

  return (
    <div>
      <PageHeader
        title="Usage report"
        subtitle="Every customer's data usage for one period, heaviest first."
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <PeriodSwitcher value={period} onChange={setPeriod} />
        {report && (
          <span className="text-sm text-[var(--text-muted)]">
            {report.period_label} · {rows.length} customer{rows.length === 1 ? "" : "s"} ·{" "}
            {formatBytes(grandTotal)} total
          </span>
        )}
      </div>

      {error ? (
        <p className="text-sm text-[var(--status-critical)]">{error}</p>
      ) : loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-[var(--text-muted)]">
          No usage recorded for {report?.period_label}.
          {report?.measuring_since
            ? " Usage has only been recorded since " +
              new Date(report.measuring_since).toLocaleDateString(undefined, {
                day: "numeric", month: "short", year: "numeric",
              }) + "."
            : " Nothing has been recorded yet — check that sample_session_usage is running on the VPS."}
        </p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Customer</TH>
              <TH>Download</TH>
              <TH>Upload</TH>
              <TH>Total</TH>
              <TH>Cap</TH>
            </tr>
          </THead>
          <tbody>
            {rows.map((r) => {
              const tone = capTone(r.cap_used_pct);
              return (
                <TR key={r.customer}>
                  <TD>
                    <Link
                      to={`/admin/customers/${r.customer}`}
                      className="font-medium text-[var(--series-1)] hover:underline"
                    >
                      {r.full_name}
                    </Link>
                    <div className="text-xs text-[var(--text-muted)]">{r.customer_ref}</div>
                  </TD>
                  <TD className="tabular-nums">{formatBytes(r.download_bytes)}</TD>
                  <TD className="tabular-nums">{formatBytes(r.upload_bytes)}</TD>
                  <TD className="font-medium tabular-nums">{formatBytes(r.total_bytes)}</TD>
                  <TD>
                    {r.cap_bytes == null ? (
                      <span className="text-xs text-[var(--text-muted)]">Uncapped</span>
                    ) : (
                      <div className="min-w-28">
                        <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--tint-hover)]">
                          <div
                            className="h-full rounded-full"
                            style={{
                              width: `${Math.min(100, r.cap_used_pct ?? 0)}%`,
                              backgroundColor: tone ?? "var(--chart-1)",
                            }}
                          />
                        </div>
                        {/* The number is always present, so the colour is a
                            second cue rather than the only one. */}
                        <p className="mt-1 text-xs text-[var(--text-muted)]">
                          {r.cap_used_pct}% of {formatBytes(r.cap_bytes)}
                        </p>
                      </div>
                    )}
                  </TD>
                </TR>
              );
            })}
          </tbody>
        </Table>
      )}

      {report?.measuring_since && rows.length > 0 && (
        <p className="mt-3 text-xs text-[var(--text-muted)]">
          Usage has only been recorded since{" "}
          {new Date(report.measuring_since).toLocaleDateString(undefined, {
            day: "numeric", month: "short", year: "numeric",
          })}
          . Earlier periods are blank because nothing was measured, not because nothing was used.
        </p>
      )}
    </div>
  );
}
