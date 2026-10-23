import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, SortableTH, TR, TD } from "../../components/Table";
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

// Sorting is server-side (the API takes `ordering`), so the order survives
// the row limit: the top 200 is always the heaviest 200, whichever column
// you then arrange them by. Sorting in the browser would only ever reorder
// the rows that happened to be fetched.
const ORDERING_KEY = "skybre.usage-report.ordering";
const DEFAULT_ORDERING = "-total_bytes";

function storedOrdering() {
  // Wrapped: a browser with site data blocked throws on access rather than
  // returning null, and a remembered sort is not worth a blank page.
  try {
    return localStorage.getItem(ORDERING_KEY) || DEFAULT_ORDERING;
  } catch {
    return DEFAULT_ORDERING;
  }
}

export function UsageReportPage() {
  const [period, setPeriod] = useState<Period>("month");
  const [ordering, setOrdering] = useState<string>(storedOrdering);
  const [report, setReport] = useState<UsageReport | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);

  // Clicking a header cycles descending -> ascending, starting on descending
  // for the figures: the first question about a usage column is always "who
  // is at the top of it". Names start ascending, because A-Z is what a name
  // column is for.
  function handleSort(field: string) {
    setOrdering((current) => {
      const textual = field === "full_name" || field === "customer_ref";
      const next =
        current === field ? `-${field}` : current === `-${field}` ? field : textual ? field : `-${field}`;
      try {
        localStorage.setItem(ORDERING_KEY, next);
      } catch {
        // Not worth a word to the user: the sort still works, it just
        // won't be here next time.
      }
      return next;
    });
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .get<UsageReport>(`/usage-report/?period=${period}&ordering=${encodeURIComponent(ordering)}`)
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
  }, [period, ordering]);

  // The export is authenticated, so it cannot be a plain <a href>: the JWT
  // lives in localStorage and only axios attaches it. Fetch the file as a
  // blob and hand the browser an object URL. Same as the billing bundle.
  async function handleExport() {
    setExporting(true);
    try {
      const res = await api.get("/usage-report/export/", {
        params: { period, ordering },
        responseType: "blob",
      });
      // The server names the file after the period it actually covers, and
      // that name is the only thing separating one month's export from
      // another in a downloads folder -- so use it rather than inventing one.
      const disposition = String(res.headers?.["content-disposition"] ?? "");
      const named = /filename="?([^";]+)"?/.exec(disposition)?.[1];
      const url = URL.createObjectURL(res.data as Blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = named || `usage_${period}.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch {
      setError("Couldn't export the usage report.");
    } finally {
      setExporting(false);
    }
  }

  const rows = report?.results ?? [];
  const grandTotal = rows.reduce((sum, r) => sum + r.total_bytes, 0);
  // The table is capped; the CSV is not. Say so rather than letting the
  // count read as the whole story.
  const truncated = (report?.total_rows ?? 0) > rows.length;

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
            {report.period_label} ·{" "}
            {truncated
              ? `heaviest ${rows.length} of ${report.total_rows} customers`
              : `${rows.length} customer${rows.length === 1 ? "" : "s"}`}{" "}
            · {formatBytes(grandTotal)} total
          </span>
        )}
        {rows.length > 0 && (
          <button
            type="button"
            className="rounded-md border border-[var(--border-hairline)] px-3 py-1.5 text-sm text-[var(--text-secondary)] hover:bg-[var(--tint-subtle)] disabled:opacity-50"
            disabled={exporting}
            onClick={handleExport}
          >
            {exporting ? "Preparing…" : "Export CSV"}
          </button>
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
              <SortableTH field="full_name" ordering={ordering} onSort={handleSort}>
                Customer
              </SortableTH>
              <SortableTH field="download_bytes" ordering={ordering} onSort={handleSort}>
                Download
              </SortableTH>
              <SortableTH field="upload_bytes" ordering={ordering} onSort={handleSort}>
                Upload
              </SortableTH>
              <SortableTH field="total_bytes" ordering={ordering} onSort={handleSort}>
                Total
              </SortableTH>
              <SortableTH field="cap_used_pct" ordering={ordering} onSort={handleSort}>
                Cap
              </SortableTH>
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
