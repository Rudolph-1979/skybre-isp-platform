import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { PdfPreviewModal } from "./PdfPreviewModal";
import { formatIsoDateTime } from "../utils/billingTerms";

// Technicians' digital job cards from the Conecto app (7 Oct 2026) -- they
// replace the paper "Jobcard / L.O.S / Installation Document". Shown on a
// client's Overview and on a job; each opens as a PDF with the client's
// signature.

export type JobCardRow = {
  id: number;
  reference: string;
  job: number | null;
  client_name: string;
  technician_name: string;
  job_type_labels: string[];
  line_of_sight: string;
  additional_work_needed: boolean;
  additional_work_ticket: { id: number; number: string } | null;
  client_signed_name: string;
  client_unavailable_reason: string;
  has_signature: boolean;
  completed_at: string | null;
  received_at: string;
};

export function JobCardsList({ customerId, jobId, compact }: { customerId?: number; jobId?: number; compact?: boolean }) {
  const [rows, setRows] = useState<JobCardRow[] | null>(null);
  const [open, setOpen] = useState<JobCardRow | null>(null);

  useEffect(() => {
    const q = customerId ? `customer=${customerId}` : `job=${jobId}`;
    api
      .get<JobCardRow[]>(`/job-cards/?${q}`)
      .then((res) => setRows(res.data))
      .catch(() => setRows([]));
  }, [customerId, jobId]);

  if (rows === null) return null;
  if (compact && rows.length === 0) return null;

  const pdf = open && (
    <PdfPreviewModal
      title={`${open.reference} — job card`}
      url={`/job-cards/${open.id}/pdf/`}
      filename={`${open.reference} job card.pdf`}
      onClose={() => setOpen(null)}
    />
  );

  if (compact) {
    return (
      <div className="mb-3 rounded-md border border-[var(--border-hairline)] bg-[var(--tint-subtle)] p-3 text-sm">
        <p className="mb-1 font-medium text-[var(--text-secondary)]">Job card{rows.length > 1 ? "s" : ""}</p>
        {rows.map((r) => (
          <button key={r.id} type="button" className="mr-3 text-[var(--series-1)] hover:underline" onClick={() => setOpen(r)}>
            {r.reference} · {r.technician_name || "technician"} · {r.completed_at ? formatIsoDateTime(r.completed_at) : formatIsoDateTime(r.received_at)}
          </button>
        ))}
        {pdf}
      </div>
    );
  }

  return (
    <div className="mt-6">
      <h2 className="mb-2 text-sm font-semibold">Job cards</h2>
      <div className="overflow-x-auto rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)]">
        <table className="w-full text-sm">
          <thead className="border-b border-[var(--border-hairline)] bg-[var(--tint-subtle)] text-left">
            <tr className="text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
              <th className="px-4 py-2.5">Completed</th>
              <th className="px-4 py-2.5">Job</th>
              <th className="px-4 py-2.5">Technician</th>
              <th className="px-4 py-2.5">Signed</th>
              <th className="px-4 py-2.5">Follow-up</th>
              <th className="px-4 py-2.5" />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b border-[var(--border-hairline)] last:border-0">
                <td className="whitespace-nowrap px-4 py-2.5">
                  {formatIsoDateTime(r.completed_at || r.received_at)}
                  <div className="text-xs text-[var(--text-muted)]">{r.reference}</div>
                </td>
                <td className="px-4 py-2.5">
                  {r.job_type_labels.join(", ") || "—"}
                  {r.line_of_sight && (
                    <div className="text-xs text-[var(--text-muted)]">{r.line_of_sight === "los" ? "Line of sight" : "No line of sight"}</div>
                  )}
                </td>
                <td className="px-4 py-2.5">{r.technician_name || "—"}</td>
                <td className="px-4 py-2.5">
                  {r.client_signed_name || (r.client_unavailable_reason ? <span className="text-[var(--text-muted)]">Not signed: {r.client_unavailable_reason}</span> : "—")}
                </td>
                <td className="px-4 py-2.5">
                  {r.additional_work_needed ? (
                    r.additional_work_ticket ? (
                      <Link to={`/admin/tickets/${r.additional_work_ticket.id}`} className="text-[var(--series-1)] hover:underline">
                        {r.additional_work_ticket.number}
                      </Link>
                    ) : (
                      <span className="text-[var(--status-warning)]">Additional work needed</span>
                    )
                  ) : (
                    "—"
                  )}
                </td>
                <td className="px-4 py-2.5 text-right">
                  <button type="button" className="text-sm font-medium text-[var(--series-1)] hover:underline" onClick={() => setOpen(r)}>
                    View
                  </button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-center text-sm text-[var(--text-muted)]">
                  No job cards yet — they arrive from the technicians' app when a job is checked out.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {pdf}
    </div>
  );
}
