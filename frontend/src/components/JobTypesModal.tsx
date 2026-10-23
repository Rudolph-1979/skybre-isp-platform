import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import { apiErrorMessage } from "../utils/apiError";
import { Modal, inputClass, btnPrimary, btnSecondary } from "./Modal";
import type { JobTypeOption } from "../types";

// Scheduling -> Job types (Rudolph, 5 Oct 2026: "Create a option to create
// job types in Scheduling"). Add, rename, switch off; delete only
// when no job uses the type (the backend says so otherwise).
export function JobTypesModal({
  types,
  onChanged,
  onClose,
}: {
  types: JobTypeOption[];
  onChanged: () => void;
  onClose: () => void;
}) {
  const [newName, setNewName] = useState("");
  const [drafts, setDrafts] = useState<Record<number, { name: string; colour: string }>>({});
  const [busy, setBusy] = useState<number | "new" | null>(null);
  const [error, setError] = useState("");

  function draftOf(t: JobTypeOption) {
    return drafts[t.id] ?? { name: t.name, colour: t.colour };
  }

  async function run(id: number | "new", fn: () => Promise<unknown>) {
    setBusy(id);
    setError("");
    try {
      await fn();
      onChanged();
      return true;
    } catch (err) {
      setError(apiErrorMessage(err, "Could not save the job type."));
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function handleAdd(e: FormEvent) {
    e.preventDefault();
    if (!newName.trim()) return;
    const ok = await run("new", () => api.post("/job-types/", { name: newName }));
    if (ok) setNewName("");
  }

  async function saveRow(t: JobTypeOption) {
    const d = draftOf(t);
    const ok = await run(t.id, () => api.patch(`/job-types/${t.id}/`, d));
    if (ok) setDrafts(({ [t.id]: _gone, ...rest }) => rest);
  }

  return (
    <Modal title="Job types" onClose={onClose}>
      <p className="mb-3 text-sm text-[var(--text-secondary)]">
        The types you can pick on a job. The calendar colours jobs by technician, and the type is written on
        the card. Renaming a type relabels the jobs that already have it. A type that's in use can't be deleted — switch it off to stop it being picked.
      </p>

      <div className="mb-4 space-y-2">
        {types.map((t) => {
          const d = draftOf(t);
          const dirty = d.name !== t.name || d.colour !== t.colour;
          return (
            <div key={t.id} className={`flex flex-wrap items-center gap-2 ${t.is_active ? "" : "opacity-60"}`}>
              <input
                className={`${inputClass} min-w-0 flex-1`}
                value={d.name}
                onChange={(e) => setDrafts({ ...drafts, [t.id]: { ...d, name: e.target.value } })}
              />
              <span className="w-16 text-right text-xs text-[var(--text-muted)]">
                {t.job_count} job{t.job_count === 1 ? "" : "s"}
              </span>
              {dirty && (
                <button type="button" className={btnPrimary} disabled={busy === t.id} onClick={() => saveRow(t)}>
                  Save
                </button>
              )}
              <label className="flex items-center gap-1 text-xs text-[var(--text-secondary)]" title="Switched off = can't be picked on a new job">
                <input
                  type="checkbox"
                  checked={t.is_active}
                  disabled={busy === t.id}
                  onChange={(e) => run(t.id, () => api.patch(`/job-types/${t.id}/`, { is_active: e.target.checked }))}
                />
                On
              </label>
              {t.job_count === 0 && (
                <button
                  type="button"
                  className="text-xs text-[var(--status-critical)] hover:underline"
                  disabled={busy === t.id}
                  onClick={() => run(t.id, () => api.delete(`/job-types/${t.id}/`))}
                >
                  Delete
                </button>
              )}
            </div>
          );
        })}
      </div>

      <form onSubmit={handleAdd} className="flex flex-wrap items-center gap-2 border-t border-[var(--border-hairline)] pt-4">
        <input
          className={`${inputClass} min-w-0 flex-1`}
          placeholder="New job type, e.g. Fibre splice"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
        <button type="submit" className={btnPrimary} disabled={busy === "new" || !newName.trim()}>
          + Add
        </button>
      </form>

      {error && <p className="mt-3 text-sm text-[var(--status-critical)]">{error}</p>}

      <div className="mt-4 flex justify-end">
        <button type="button" className={btnSecondary} onClick={onClose}>
          Done
        </button>
      </div>
    </Modal>
  );
}
