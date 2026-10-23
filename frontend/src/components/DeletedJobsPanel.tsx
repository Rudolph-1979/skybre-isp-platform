import { useState } from "react";
import { api } from "../api/client";
import { apiErrorMessage } from "../utils/apiError";
import type { Job } from "../types";

// Deleted jobs (Rudolph, 6 Oct 2026: "a job that is deleted accidentally
// must move to a pre delete job pool and only final delete once deleted
// from delete pool"). Deleting a job anywhere moves it here: off the
// calendar and off technicians' phones. Restore puts it back exactly where
// it was; "Delete forever" is the only real delete.
export function DeletedJobsPanel({
  jobs,
  onChanged,
}: {
  jobs: Job[];
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState("");

  async function act(job: Job, kind: "restore" | "purge") {
    if (
      kind === "purge" &&
      !confirm(`Delete "${job.title}" for good?\n\nThis can't be undone.`)
    )
      return;
    setBusy(job.id);
    setError("");
    try {
      if (kind === "restore") await api.post(`/jobs/${job.id}/restore/`);
      else await api.delete(`/jobs/${job.id}/purge/`);
      onChanged();
    } catch (err) {
      setError(apiErrorMessage(err, kind === "restore" ? "Couldn't restore that job." : "Couldn't delete that job."));
    } finally {
      setBusy(null);
    }
  }

  const when = (iso: string | null | undefined) =>
    iso
      ? new Date(iso).toLocaleString("en-ZA", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })
      : "";

  return (
    <aside className="w-full shrink-0 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-3 shadow-sm lg:w-72">
      <button type="button" className="flex w-full items-center justify-between" onClick={() => setOpen((v) => !v)}>
        <h3 className="text-sm font-semibold text-[var(--text-primary)]">
          {open ? "▾" : "▸"} Deleted jobs
        </h3>
        <span
          className={`rounded-full px-2 py-0.5 text-xs font-medium ${
            jobs.length ? "bg-[#3a1c1c] text-[#ff9a9a]" : "bg-[var(--tint-subtle)] text-[var(--text-muted)]"
          }`}
        >
          {jobs.length}
        </span>
      </button>
      {open && (
        <>
          <p className="mb-3 mt-1 text-xs text-[var(--text-muted)]">
            Deleted by mistake? Restore puts the job back where it was, and back on the technician's phone. Only
            "Delete forever" removes it for good.
          </p>
          {error && <p className="mb-2 text-xs text-[var(--status-critical)]">{error}</p>}
          {jobs.length === 0 ? (
            <p className="text-xs text-[var(--text-muted)]">Nothing deleted.</p>
          ) : (
            <ul className="max-h-[50vh] space-y-2 overflow-y-auto">
              {jobs.map((j) => (
                <li key={j.id} className="rounded-md border border-dashed border-[var(--border-hairline)] bg-[var(--surface-2)] p-2 text-xs">
                  <span className="block truncate font-medium text-[var(--text-primary)]">{j.title}</span>
                  <span className="block truncate text-[var(--text-muted)]">
                    {j.start ? `Was ${when(j.start)}` : "Was in the job pool"}
                    {j.technicians.length ? ` · ${j.technicians.map((t) => t.name).join(", ")}` : ""}
                  </span>
                  <span className="block truncate text-[var(--text-muted)]">
                    Deleted {when(j.deleted_at)}
                    {j.deleted_by_name ? ` by ${j.deleted_by_name}` : ""}
                  </span>
                  <div className="mt-1.5 flex gap-2">
                    <button
                      type="button"
                      disabled={busy === j.id}
                      onClick={() => act(j, "restore")}
                      className="flex-1 rounded bg-[var(--series-1)] px-2 py-1 text-[11px] font-medium text-white hover:opacity-90 disabled:opacity-50"
                    >
                      Restore
                    </button>
                    <button
                      type="button"
                      disabled={busy === j.id}
                      onClick={() => act(j, "purge")}
                      className="flex-1 rounded border border-[var(--status-critical)] px-2 py-1 text-[11px] font-medium text-[var(--status-critical)] hover:bg-[#3a1c1c] disabled:opacity-50"
                    >
                      Delete forever
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </aside>
  );
}
