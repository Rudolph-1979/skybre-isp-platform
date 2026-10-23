import type { Job } from "../types";

// The Job pool -- Rudolph, 30 Sep: "create a Job pool where jobs land
// before scheduled... Jobs can then be pulled from the pool to better
// schedule route and times."
//
// A pool job is a Job with no start/end and no assigned_technicians (see
// scheduling/serializers.py's JobSerializer.validate for why the backend
// enforces that pairing). This panel is a persistent side panel -- visible
// next to both the Month and Day views, per Rudolph's own answer on where
// it should live -- listing whatever is waiting, oldest first (the parent
// fetches with &ordering=created_at, same FIFO order the backend test
// pins down).
//
// Two ways out of the pool, both wired here: drag a card onto a day (month
// view) or an hour (day view) -- draggable/onDragStart below puts the
// job's id on the drag event, and SchedulingPage/DayScheduleGrid look it up
// and open the edit modal pre-filled at the drop target, so the office
// still gets to check the time and assign a technician before Save. Or
// press Schedule, which opens the same modal pre-filled at "now".
export function JobPoolPanel({
  jobs,
  loading,
  jobColour,
  onScheduleJob,
  onEditJob,
  onSettings,
}: {
  jobs: Job[];
  loading: boolean;
  jobColour: (job: Job) => string;
  onScheduleJob: (job: Job) => void;
  onEditJob: (job: Job) => void;
  /** Opens the daily-return settings (6 Oct 2026). */
  onSettings?: () => void;
}) {
  return (
    <aside className="w-full shrink-0 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-3 shadow-sm lg:w-72">
      <div className="mb-1 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-[var(--text-primary)]">Job pool</h3>
        <span className="rounded-full bg-[var(--tint-subtle)] px-2 py-0.5 text-xs font-medium text-[var(--text-muted)]">
          {jobs.length}
        </span>
      </div>
      <p className="mb-3 text-xs text-[var(--text-muted)]">
        Waiting for a time and a technician. Drag a job onto a day or hour, or press Schedule.
        {onSettings && (
          <>
            {" "}
            <button type="button" className="text-[var(--series-1)] hover:underline" onClick={onSettings}>
              Pool settings
            </button>
          </>
        )}
      </p>

      {loading ? (
        <p className="text-xs text-[var(--text-muted)]">Loading…</p>
      ) : jobs.length === 0 ? (
        <p className="text-xs text-[var(--text-muted)]">Nothing in the pool right now.</p>
      ) : (
        <ul className="max-h-[70vh] space-y-2 overflow-y-auto">
          {/* Urgent first (15 Oct 2026), otherwise the order they came in. */}
          {[...jobs].sort((a, b) => Number(Boolean(b.urgent)) - Number(Boolean(a.urgent))).map((j) => (
            <li
              key={j.id}
              draggable
              onDragStart={(e) => {
                e.dataTransfer.setData("text/plain", String(j.id));
                e.dataTransfer.effectAllowed = "move";
              }}
              title="Drag onto a day or hour to schedule it there"
              className="cursor-grab rounded-md border border-[var(--border-hairline)] bg-[var(--surface-2)] p-2 text-xs active:cursor-grabbing"
              style={j.urgent ? { outline: "2px solid #ff3b3b", outlineOffset: -1 } : undefined}
            >
              <button type="button" onClick={() => onEditJob(j)} className="block w-full text-left">
                <span className="mb-0.5 flex items-center gap-1.5 font-medium text-[var(--text-primary)]">
                  <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: jobColour(j) }} />
                  {j.urgent && <span className="shrink-0 rounded-sm bg-[#ff3b3b] px-1 text-[10px] font-bold text-white">URGENT</span>}
                  <span className="truncate">{j.title}</span>
                </span>
                <span className="block truncate text-[var(--text-muted)]">
                  {j.customer_name ?? "Standalone"}
                </span>
                {/* Why it's back (a technician's Return, or the daily
                    sweep of uncompleted jobs). */}
                {j.return_reason && (
                  <span className="mt-0.5 block text-[#e0a83c]" title={j.return_reason}>
                    ↩ {j.return_reason}
                    {(j.return_count ?? 0) > 1 ? ` (returned ${j.return_count}×)` : ""}
                  </span>
                )}
              </button>
              <button
                type="button"
                onClick={() => onScheduleJob(j)}
                className="mt-1.5 w-full rounded bg-[var(--series-1)] px-2 py-1 text-[11px] font-medium text-white hover:opacity-90"
              >
                Schedule
              </button>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}
