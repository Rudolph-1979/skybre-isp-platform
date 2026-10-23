import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import type { TicketKpiReport } from "../../types";

/**
 * Yearly ticket KPI: who accepted what, and how much of it they closed out
 * clean -- the numbers Rudolph asked to feed the annual bonus calculation.
 *
 * "Resolved without escalation" is deliberately not "resolved" alone: a
 * ticket that got escalated and NOC later rejected it back (solvable by
 * support after all) still doesn't count as a clean resolution here --
 * the escalation happened, which is the fact this metric is about,
 * regardless of how it was decided. See TicketViewSet.kpi_report.
 *
 * Two more things Rudolph asked to fold into this same page, 26 Sep:
 * NOC as its own dimension, scored by tickets resolved per operator, and
 * a Score column for both dimensions -- volume divided by how quickly
 * those tickets got resolved on average, so raw ticket count alone
 * doesn't out-rank someone who resolves fewer but faster. A ticket that's
 * ever reopened stops counting toward either dimension's numbers at all,
 * permanently -- see the footnote below and Ticket.disqualified_from_kpi.
 */

function formatHours(hours: number | null): string {
  if (hours == null) return "—";
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  if (hours < 48) return `${hours.toFixed(1)}h`;
  return `${(hours / 24).toFixed(1)}d`;
}

function formatScore(score: number | null): string {
  return score == null ? "—" : score.toFixed(2);
}

const CURRENT_YEAR = new Date().getFullYear();

export function TicketKpiReportPage() {
  const [year, setYear] = useState(CURRENT_YEAR);
  const [report, setReport] = useState<TicketKpiReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .get<TicketKpiReport>(`/tickets/kpi-report/?year=${year}`)
      .then((res) => {
        if (cancelled) return;
        setReport(res.data);
        setError("");
      })
      .catch(() => {
        if (!cancelled) setError("Couldn't load the KPI report.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [year]);

  const rows = report?.results ?? [];
  const nocRows = report?.noc_results ?? [];

  return (
    <div>
      <PageHeader
        title="Ticket KPI report"
        subtitle="Tickets accepted and resolved cleanly per staff member, by year."
      />

      <div className="mb-4 flex items-center gap-2">
        <button
          type="button"
          className="rounded-md border border-[var(--border-hairline)] px-2 py-1 text-sm hover:bg-[var(--tint-subtle)]"
          onClick={() => setYear((y) => y - 1)}
        >
          ← {year - 1}
        </button>
        <span className="text-sm font-medium text-[var(--text-primary)]">{year}</span>
        <button
          type="button"
          className="rounded-md border border-[var(--border-hairline)] px-2 py-1 text-sm hover:bg-[var(--tint-subtle)] disabled:opacity-40"
          disabled={year >= CURRENT_YEAR}
          onClick={() => setYear((y) => y + 1)}
        >
          {year + 1} →
        </button>
        {year !== CURRENT_YEAR && (
          <button
            type="button"
            className="text-sm text-[var(--series-1)] hover:underline"
            onClick={() => setYear(CURRENT_YEAR)}
          >
            This year
          </button>
        )}
      </div>

      {error ? (
        <p className="text-sm text-[var(--status-critical)]">{error}</p>
      ) : loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <>
          <h2 className="mb-2 text-sm font-semibold">Support</h2>
          {rows.length === 0 ? (
            <p className="text-sm text-[var(--text-muted)]">No tickets were accepted by anyone in {year}.</p>
          ) : (
            <Table>
              <THead>
                <tr>
                  <TH>Staff member</TH>
                  <TH>Tickets accepted</TH>
                  <TH>Resolved without escalation</TH>
                  <TH>Clean resolution rate</TH>
                  <TH>Avg. resolution time</TH>
                  <TH>Score</TH>
                </tr>
              </THead>
              <tbody>
                {rows.map((r) => {
                  const rate = r.tickets_accepted > 0 ? (r.tickets_resolved_without_escalation / r.tickets_accepted) * 100 : null;
                  return (
                    <TR key={r.user_id}>
                      <TD className="font-medium">{r.username}</TD>
                      <TD className="tabular-nums">{r.tickets_accepted}</TD>
                      <TD className="tabular-nums">{r.tickets_resolved_without_escalation}</TD>
                      <TD className="tabular-nums">{rate == null ? "—" : `${rate.toFixed(0)}%`}</TD>
                      <TD className="tabular-nums">{formatHours(r.avg_resolution_hours)}</TD>
                      <TD className="tabular-nums font-medium">{formatScore(r.score)}</TD>
                    </TR>
                  );
                })}
              </tbody>
            </Table>
          )}

          <h2 className="mb-2 mt-6 text-sm font-semibold">NOC</h2>
          {nocRows.length === 0 ? (
            <p className="text-sm text-[var(--text-muted)]">No escalations were accepted by anyone in {year}.</p>
          ) : (
            <Table>
              <THead>
                <tr>
                  <TH>NOC member</TH>
                  <TH>Escalations accepted</TH>
                  <TH>Tickets resolved</TH>
                  <TH>Avg. resolution time</TH>
                  <TH>Score</TH>
                </tr>
              </THead>
              <tbody>
                {nocRows.map((r) => (
                  <TR key={r.user_id}>
                    <TD className="font-medium">{r.username}</TD>
                    <TD className="tabular-nums">{r.escalations_accepted}</TD>
                    <TD className="tabular-nums">{r.tickets_resolved}</TD>
                    <TD className="tabular-nums">{formatHours(r.avg_resolution_hours)}</TD>
                    <TD className="tabular-nums font-medium">{formatScore(r.score)}</TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          )}
        </>
      )}

      <p className="mt-3 text-xs text-[var(--text-muted)]">
        "Resolved without escalation" counts a ticket only if it was accepted by this person, resolved or closed in{" "}
        {year}, and never escalated to NOC at all -- an escalation NOC later rejected as solvable by support still
        counts against this number, since the escalation itself is what's being measured. NOC's "tickets resolved"
        is the mirror image: escalations this person accepted and that went on to be resolved while assigned to them.
      </p>
      <p className="mt-2 text-xs text-[var(--text-muted)]">
        Score is tickets resolved divided by the average time to resolve them -- a bigger number means more tickets,
        resolved faster. A ticket that's ever reopened -- by the customer, by staff, or by NOC accepting an
        escalation on one already closed out -- stops counting as resolved for this report at all, for anyone, even
        once it's resolved again.
      </p>
    </div>
  );
}
