import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import { formatIsoDateTime } from "../utils/billingTerms";
import { inputClass } from "./Modal";

type Change = { field: string; label: string; from: string; to: string };
type Entry = {
  id: number;
  at: string;
  who: string;
  action: "created" | "updated" | "deleted" | string;
  job: number | null;
  job_exists: boolean;
  label: string;
  changes: Change[];
  detail: string;
};

const DATE_LIKE = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/;
// Bookkeeping columns the headline already says in words.
const QUIET = new Set(["deleted_at", "deleted_by", "updated_at"]);

function show(value: string, field: string) {
  if (!value) return "—";
  if (DATE_LIKE.test(value)) {
    const d = value.replace(" ", "T");
    return Number.isNaN(Date.parse(d)) ? value : formatIsoDateTime(d);
  }
  if (field === "description" && value.length > 60) return `${value.slice(0, 60)}…`;
  return value;
}

/** One line saying what happened, from the fields that moved. */
function headline(e: Entry): { text: string; tone: string } {
  if (e.action === "created") return { text: "Created", tone: "var(--status-good, #22c55e)" };
  if (e.action === "deleted") return { text: "Deleted for good", tone: "var(--status-critical)" };
  const by = Object.fromEntries(e.changes.map((c) => [c.field, c]));
  if (by.deleted_at) {
    return by.deleted_at.to
      ? { text: "Deleted (to Deleted jobs)", tone: "var(--status-critical)" }
      : { text: "Restored", tone: "var(--status-good, #22c55e)" };
  }
  if (by.start) {
    if (!by.start.from) return { text: "Scheduled", tone: "var(--series-1)" };
    if (!by.start.to) return { text: "Moved to pool", tone: "var(--status-warning)" };
    return { text: "Rescheduled", tone: "var(--series-1)" };
  }
  if (by.assigned_technicians) return { text: "Technicians changed", tone: "var(--series-1)" };
  if (by.status) return { text: `Status → ${by.status.to}`, tone: "var(--text-secondary)" };
  return { text: "Changed", tone: "var(--text-secondary)" };
}

function changeText(c: Change) {
  if (c.field === "assigned_technicians") {
    // m2m rows: "from" is "added: X" / "removed: X"; "to" is everyone now.
    return `${c.label}: ${c.from}${c.to ? ` (now ${c.to})` : ""}`;
  }
  return `${c.label}: ${show(c.from, c.field)} → ${show(c.to, c.field)}`;
}

/** Scheduling page, under the calendar (Rudolph, 8 Oct 2026: "add a Job
 *  change & Changelog tile to see who schedule, changed the jobs etc").
 *  Newest first, 25 at a time; refreshes every 30 s and whenever the
 *  calendar reloads. Read from the audit trail, so it starts from the day
 *  this was installed. */
export function JobChangelogPanel({ refreshKey, onOpenJob }: { refreshKey: unknown; onOpenJob: (id: number) => void }) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(
    (wantedPage: number, quiet = false) => {
      if (!quiet) setLoading(true);
      const params = new URLSearchParams({ page: String(wantedPage) });
      if (q.trim()) params.set("q", q.trim());
      api
        .get<{ results: Entry[]; has_more: boolean }>(`/jobs/changelog/?${params}`)
        .then(({ data }) => {
          setEntries((prev) => (wantedPage === 1 ? data.results : [...prev, ...data.results]));
          setHasMore(data.has_more);
          setPage(wantedPage);
          setError("");
        })
        .catch(() => setError("Couldn't load the changelog."))
        .finally(() => setLoading(false));
    },
    [q]
  );

  // Search: wait for typing to settle.
  useEffect(() => {
    const t = window.setTimeout(() => load(1), 300);
    return () => window.clearTimeout(t);
  }, [load]);
  // The calendar just reloaded (a save, a drag, a delete): show it here too.
  useEffect(() => {
    load(1, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);
  usePolling(() => {
    if (page === 1) load(1, true);
  }, 30000);

  return (
    <div className="mt-4 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)]">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border-hairline)] px-3 py-2">
        <div>
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">Job changes &amp; changelog</h3>
          <p className="text-xs text-[var(--text-muted)]">Who booked, moved, re-assigned, deleted or restored a job.</p>
        </div>
        <input
          className={`${inputClass} max-w-xs`}
          placeholder="Search job or person…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>
      {error && <p className="px-3 py-2 text-sm text-[var(--status-critical)]">{error}</p>}
      {!error && entries.length === 0 && !loading && (
        <p className="px-3 py-3 text-sm text-[var(--text-muted)]">
          {q ? "Nothing matches." : "No job changes recorded yet — they show here from now on."}
        </p>
      )}
      <ul className="divide-y divide-[var(--border-hairline)]">
        {entries.map((e) => {
          const h = headline(e);
          const changes = e.changes.filter((c) => !QUIET.has(c.field));
          return (
            <li key={e.id} className="grid grid-cols-1 gap-1 px-3 py-2 text-sm sm:grid-cols-[150px_170px_1fr]">
              <span className="whitespace-nowrap text-xs text-[var(--text-muted)]">{formatIsoDateTime(e.at)}</span>
              <span className="truncate font-medium text-[var(--text-primary)]" title={e.who}>
                {e.who}
              </span>
              <span className="min-w-0">
                <span className="mr-2 font-medium" style={{ color: h.tone }}>
                  {h.text}
                </span>
                {e.job_exists && e.job ? (
                  <button
                    type="button"
                    className="text-left text-[var(--series-1)] hover:underline"
                    onClick={() => onOpenJob(e.job!)}
                  >
                    {e.label}
                  </button>
                ) : (
                  <span className="text-[var(--text-secondary)]">{e.label}</span>
                )}
                {changes.length > 0 && (
                  <span className="mt-0.5 block text-xs text-[var(--text-muted)]">
                    {changes.map(changeText).join(" · ")}
                  </span>
                )}
              </span>
            </li>
          );
        })}
      </ul>
      {hasMore && (
        <div className="border-t border-[var(--border-hairline)] px-3 py-2 text-center">
          <button type="button" className="text-sm text-[var(--series-1)] hover:underline" onClick={() => load(page + 1)}>
            Load older changes
          </button>
        </div>
      )}
    </div>
  );
}
