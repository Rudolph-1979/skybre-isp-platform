import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { btnPrimary, btnSecondary } from "./Modal";
import { StatusBadge } from "./StatusBadge";
import { formatIsoDateTime } from "../utils/billingTerms";
import type { Job } from "../types";

/** Jobs booked from this ticket, and the buttons to book one (Rudolph,
 *  8 Oct 2026: "create a in pool schedule job from a ticket" / "This ticket
 *  requires to be rescheduled and I need a option to schedule here before I
 *  resolve"). Both open New job on the Scheduling page filled in from the
 *  ticket -- for a technician's ticket, from the job it was raised on --
 *  and Save comes back here. Hidden for staff without Scheduling. */
export function TicketJobsPanel({ ticketId }: { ticketId: number }) {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [allowed, setAllowed] = useState(true);

  useEffect(() => {
    api
      .get<{ results: Job[] }>(`/jobs/?ticket=${ticketId}&ordering=-created_at&page_size=50`)
      .then((r) => setJobs(r.data.results))
      .catch(() => setAllowed(false));
  }, [ticketId]);

  if (!allowed) return null;
  return (
    <div className="mb-4 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] px-3 py-2 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium text-[var(--text-primary)]">Jobs</span>
        <div className="flex gap-2">
          <Link to={`/admin/scheduling?fromTicket=${ticketId}&pool=1`} className={btnSecondary}>
            Add to job pool
          </Link>
          <Link to={`/admin/scheduling?fromTicket=${ticketId}`} className={btnPrimary}>
            Schedule job
          </Link>
        </div>
      </div>
      {jobs && jobs.length > 0 ? (
        <ul className="mt-2 divide-y divide-[var(--border-hairline)]">
          {jobs.map((j) => (
            <li key={j.id} className="flex flex-wrap items-center justify-between gap-2 py-1.5">
              <span className="text-[var(--text-primary)]">{j.title}</span>
              <span className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
                {j.start ? formatIsoDateTime(j.start) : "In pool"}
                {j.technicians.length > 0 && ` · ${j.technicians.map((t) => t.name).join(", ")}`}
                {j.start && <StatusBadge status={j.status} />}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-xs text-[var(--text-muted)]">{jobs ? "No job booked from this ticket yet." : "Loading…"}</p>
      )}
    </div>
  );
}
