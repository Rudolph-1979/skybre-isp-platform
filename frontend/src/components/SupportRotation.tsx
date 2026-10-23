import { useEffect, useState } from "react";
import { api } from "../api/client";
import { apiErrorMessage } from "../utils/apiError";
import { staffFullName } from "../utils/scheduling";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "./Modal";
import type { SupportPerson, SupportRotation, SupportWeek, User } from "../types";

// Weekly support rotations (#184, 16 Oct 2026). Rudolph: "We have staff
// that is on weekly support rotating ... 2 is on vehicle support and one is
// on phone support". Who is on is worked out by the backend
// (scheduling/rotation.py); this file only shows it and edits the setup.

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function rotationIcon(name: string) {
  const n = name.toLowerCase();
  if (n.includes("vehicle") || n.includes("field")) return "🚗";
  if (n.includes("phone") || n.includes("call")) return "📞";
  return "🎧";
}

function dmy(iso: string) {
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${y}`;
}

/** The weeks that cover a calendar day (yyyy-mm-dd), one per rotation. */
export function supportWeeksForDay(weeks: SupportWeek[], dayKey: string) {
  return weeks.filter((w) => w.week_start <= dayKey && dayKey < w.week_end);
}

function personLabel(p: SupportPerson) {
  return p.instead_of ? `${p.name} (${p.kind === "swap" ? "swapped with" : "covering"} ${p.instead_of})` : p.name;
}

/** One rotation's strip at the top of a month-grid cell. The names are
 *  written on the week's first day and on Sunday (the first column), so a
 *  week reads as one band across the row. */
export function SupportStrip({
  week,
  showNames,
  onOpen,
}: {
  week: SupportWeek;
  showNames: boolean;
  onOpen: (w: SupportWeek) => void;
}) {
  const changed = week.people.some((p) => p.override_id);
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onOpen(week);
      }}
      className={`-mx-1.5 mb-0.5 flex h-4 w-[calc(100%+0.75rem)] items-center gap-1 overflow-hidden whitespace-nowrap bg-[var(--tint-subtle)] px-1.5 text-left text-[10px] leading-4 text-[var(--text-secondary)] hover:bg-[var(--tint-hover)] ${
        changed ? "outline-dashed outline-1 -outline-offset-1 outline-[var(--text-muted)]" : ""
      }`}
      title={`${week.rotation_name}: ${week.people.map(personLabel).join(", ") || "nobody"} — ${dmy(week.starts_at)} ${week.starts_at.slice(11, 16)} to ${dmy(week.ends_at)} ${week.ends_at.slice(11, 16)}. Click to cover or swap.`}
    >
      <span>{rotationIcon(week.rotation_name)}</span>
      {showNames &&
        week.people.map((p) => (
          <span
            key={`${p.id}-${p.override_id ?? ""}`}
            className="rounded-full px-1.5 font-semibold text-white"
            style={{ background: p.colour }}
          >
            {p.name}
          </span>
        ))}
    </button>
  );
}

/** Side-panel card: who is on support right now, and next week. */
export function SupportNowCard({ refreshKey, onManage }: { refreshKey: unknown; onManage: () => void }) {
  const [data, setData] = useState<{ rotation: number; rotation_name: string; current: SupportWeek; next: SupportWeek }[] | null>(null);
  useEffect(() => {
    api
      .get<{ rotations: { rotation: number; rotation_name: string; current: SupportWeek; next: SupportWeek }[] }>(
        "/support-rotations/now/",
      )
      .then((r) => setData(r.data.rotations))
      .catch(() => setData([]));
  }, [refreshKey]);

  return (
    <aside className="w-full shrink-0 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-3 shadow-sm lg:w-72">
      <div className="mb-1 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-[var(--text-primary)]">On support now</h3>
        <button type="button" className="text-xs text-[var(--series-1)] hover:underline" onClick={onManage}>
          Rotations
        </button>
      </div>
      {data === null ? (
        <p className="text-xs text-[var(--text-muted)]">Loading…</p>
      ) : data.length === 0 ? (
        <p className="text-xs text-[var(--text-muted)]">
          No support rotation yet.{" "}
          <button type="button" className="text-[var(--series-1)] hover:underline" onClick={onManage}>
            Set one up
          </button>
        </p>
      ) : (
        <>
          <p className="mb-2 text-xs text-[var(--text-muted)]">
            {dmy(data[0].current.starts_at)} {data[0].current.starts_at.slice(11, 16)} → {dmy(data[0].current.ends_at)}{" "}
            {data[0].current.ends_at.slice(11, 16)}
          </p>
          {data.map((r) => (
            <div key={r.rotation} className="border-t border-[var(--border-hairline)] py-1.5 text-xs">
              <div className="font-semibold text-[var(--text-primary)]">
                {rotationIcon(r.rotation_name)} {r.rotation_name}
              </div>
              {r.current.people.length === 0 && <div className="text-[var(--text-muted)]">Nobody</div>}
              {r.current.people.map((p) => (
                <div key={`${p.id}-${p.override_id ?? ""}`} className="flex items-center justify-between gap-2">
                  <span className="inline-flex items-center gap-1.5 text-[var(--text-primary)]">
                    <span className="h-2 w-2 rounded-full" style={{ background: p.colour }} />
                    {personLabel(p)}
                  </span>
                  {p.phone && (
                    <a className="text-[var(--series-1)] hover:underline" href={`tel:${p.phone}`}>
                      {p.phone}
                    </a>
                  )}
                </div>
              ))}
              <div className="mt-0.5 text-[var(--text-muted)]">
                Next: {r.next.people.map(personLabel).join(", ") || "nobody"}
              </div>
            </div>
          ))}
        </>
      )}
    </aside>
  );
}

/** Click a strip: cover or swap one person for that week, or undo. */
export function SupportWeekModal({
  week,
  staff,
  rotation,
  onClose,
  onChanged,
}: {
  week: SupportWeek;
  staff: User[];
  rotation: SupportRotation | undefined;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [mode, setMode] = useState<Record<number, "cover" | "swap">>({});
  const [pick, setPick] = useState<Record<number, string>>({});
  const [reason, setReason] = useState<Record<number, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const onIds = new Set(week.people.map((p) => p.id));
  const memberIds = new Set((rotation?.members ?? []).map((m) => m.id));

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      onChanged();
      onClose();
    } catch (err) {
      setError(apiErrorMessage(err, "Could not save that."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`${rotationIcon(week.rotation_name)} ${week.rotation_name} — week of ${dmy(week.week_start)}`} onClose={onClose}>
      <p className="mb-3 text-xs text-[var(--text-muted)]">
        {dmy(week.starts_at)} {week.starts_at.slice(11, 16)} to {dmy(week.ends_at)} {week.ends_at.slice(11, 16)}. A cover or
        swap changes only this person in this week; the rotation order stays the same.
      </p>
      {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
      {week.people.length === 0 && <p className="text-sm text-[var(--text-muted)]">Nobody is on this week.</p>}
      {week.people.map((p) => {
        const m = mode[p.id] ?? "cover";
        const options = staff.filter((s) => !onIds.has(s.id) && (m === "cover" || memberIds.has(s.id)));
        return (
          <div key={`${p.id}-${p.override_id ?? ""}`} className="mb-3 rounded-md border border-[var(--border-hairline)] p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className="inline-flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
                <span className="h-2.5 w-2.5 rounded-full" style={{ background: p.colour }} />
                {personLabel(p)}
              </span>
              {p.phone && <span className="text-xs text-[var(--text-muted)]">{p.phone}</span>}
            </div>
            {p.override_id ? (
              <div className="flex items-center justify-between gap-2 text-xs text-[var(--text-muted)]">
                <span>{p.reason ? `Reason: ${p.reason}` : "No reason given."}</span>
                <button
                  type="button"
                  className={btnSecondary}
                  disabled={busy}
                  onClick={() => run(() => api.delete(`/support-rotations/${week.rotation}/overrides/${p.override_id}/`))}
                >
                  Undo {p.kind === "swap" ? "swap (both weeks)" : "cover"}
                </button>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                <select
                  className={`${inputClass} w-auto`}
                  value={m}
                  onChange={(e) => setMode({ ...mode, [p.id]: e.target.value as "cover" | "swap" })}
                >
                  <option value="cover">Covered by</option>
                  <option value="swap">Swap week with</option>
                </select>
                <select
                  className={`${inputClass} w-auto`}
                  value={pick[p.id] ?? ""}
                  onChange={(e) => setPick({ ...pick, [p.id]: e.target.value })}
                >
                  <option value="">Pick…</option>
                  {options.map((s) => (
                    <option key={s.id} value={s.id}>
                      {staffFullName(s)}
                    </option>
                  ))}
                </select>
                <input
                  className={`${inputClass} w-40`}
                  placeholder="Reason (optional)"
                  value={reason[p.id] ?? ""}
                  onChange={(e) => setReason({ ...reason, [p.id]: e.target.value })}
                />
                <button
                  type="button"
                  className={btnPrimary}
                  disabled={busy || !pick[p.id]}
                  onClick={() =>
                    run(() =>
                      m === "cover"
                        ? api.post(`/support-rotations/${week.rotation}/cover/`, {
                            week_start: week.week_start,
                            user: p.id,
                            replacement: Number(pick[p.id]),
                            reason: reason[p.id] ?? "",
                          })
                        : api.post(`/support-rotations/${week.rotation}/swap/`, {
                            week_start: week.week_start,
                            user: p.id,
                            with_user: Number(pick[p.id]),
                            reason: reason[p.id] ?? "",
                          }),
                    )
                  }
                >
                  Save
                </button>
                {m === "swap" && (
                  <p className="w-full text-xs text-[var(--text-muted)]">
                    {p.name} takes the other person's next own week in return.
                  </p>
                )}
              </div>
            )}
          </div>
        );
      })}
      <div className="flex justify-end">
        <button type="button" className={btnSecondary} onClick={onClose}>
          Close
        </button>
      </div>
    </Modal>
  );
}

type Draft = {
  id: number | null;
  name: string;
  people_per_week: string;
  handover_weekday: number;
  handover_time: string;
  first_week_start: string;
  is_active: boolean;
  member_ids: number[];
};

function nextWeekday(weekday: number) {
  const d = new Date();
  const js = (weekday + 1) % 7; // backend 0 = Monday; JS 0 = Sunday
  d.setDate(d.getDate() + ((js - d.getDay() + 7) % 7));
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function toDraft(r: SupportRotation | null): Draft {
  if (!r)
    return {
      id: null,
      name: "",
      people_per_week: "1",
      handover_weekday: 0,
      handover_time: "08:00",
      first_week_start: nextWeekday(0),
      is_active: true,
      member_ids: [],
    };
  return {
    id: r.id,
    name: r.name,
    people_per_week: String(r.people_per_week),
    handover_weekday: r.handover_weekday,
    handover_time: r.handover_time.slice(0, 5),
    first_week_start: r.first_week_start,
    is_active: r.is_active,
    member_ids: r.members.map((m) => m.id),
  };
}

/** Scheduling -> Support rotation: the setup. */
export function SupportRotationModal({
  staff,
  onClose,
  onChanged,
}: {
  staff: User[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [rotations, setRotations] = useState<SupportRotation[] | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [addId, setAddId] = useState("");
  const [preview, setPreview] = useState<SupportWeek[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function load() {
    api
      .get<SupportRotation[]>("/support-rotations/")
      .then((r) => setRotations(r.data))
      .catch(() => setRotations([]));
    const from = new Date();
    const to = new Date();
    to.setDate(to.getDate() + 42);
    const iso = (d: Date) =>
      `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    api
      .get<{ weeks: SupportWeek[] }>(`/support-rotations/schedule/?from=${iso(from)}&to=${iso(to)}`)
      .then((r) => setPreview(r.data.weeks))
      .catch(() => setPreview([]));
  }
  useEffect(load, []);

  const byId = new Map(staff.map((s) => [s.id, s]));

  function move(i: number, delta: number) {
    if (!draft) return;
    const ids = [...draft.member_ids];
    const j = i + delta;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    setDraft({ ...draft, member_ids: ids });
  }

  async function save() {
    if (!draft) return;
    setBusy(true);
    setError("");
    const body = {
      name: draft.name.trim(),
      people_per_week: Number(draft.people_per_week) || 1,
      handover_weekday: draft.handover_weekday,
      handover_time: draft.handover_time,
      first_week_start: draft.first_week_start,
      is_active: draft.is_active,
      member_ids: draft.member_ids,
    };
    try {
      if (draft.id) await api.patch(`/support-rotations/${draft.id}/`, body);
      else await api.post("/support-rotations/", body);
      setDraft(null);
      load();
      onChanged();
    } catch (err) {
      setError(apiErrorMessage(err, "Could not save the rotation."));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: number) {
    setBusy(true);
    setError("");
    try {
      await api.delete(`/support-rotations/${id}/`);
      setDraft(null);
      load();
      onChanged();
    } catch (err) {
      setError(apiErrorMessage(err, "Could not delete the rotation."));
    } finally {
      setBusy(false);
    }
  }

  const weekStarts = [...new Set(preview.map((w) => w.week_start))].sort().slice(0, 6);

  return (
    <Modal title="Support rotation" onClose={onClose}>
      <p className="mb-3 text-xs text-[var(--text-muted)]">
        Who is on weekly support, in order. Each rotation repeats on its own every week. A cover or swap (click a support strip
        on the calendar) changes one week only.
      </p>
      {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}

      {draft ? (
        <div>
          <FormField label="Name">
            <input
              className={inputClass}
              placeholder="e.g. Vehicle support"
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            />
          </FormField>
          <div className="grid grid-cols-2 gap-3">
            <FormField label="People per week">
              <input
                className={inputClass}
                type="number"
                min={1}
                value={draft.people_per_week}
                onChange={(e) => setDraft({ ...draft, people_per_week: e.target.value })}
              />
            </FormField>
            <FormField label="Active">
              <label className="flex items-center gap-2 py-2 text-sm text-[var(--text-primary)]">
                <input
                  type="checkbox"
                  checked={draft.is_active}
                  onChange={(e) => setDraft({ ...draft, is_active: e.target.checked })}
                />
                Show on the calendar
              </label>
            </FormField>
            <FormField label="Handover day">
              <select
                className={inputClass}
                value={draft.handover_weekday}
                onChange={(e) => {
                  const wd = Number(e.target.value);
                  setDraft({ ...draft, handover_weekday: wd, first_week_start: nextWeekday(wd) });
                }}
              >
                {WEEKDAYS.map((w, i) => (
                  <option key={w} value={i}>
                    {w}
                  </option>
                ))}
              </select>
            </FormField>
            <FormField label="Handover time">
              <input
                className={inputClass}
                type="time"
                value={draft.handover_time}
                onChange={(e) => setDraft({ ...draft, handover_time: e.target.value })}
              />
            </FormField>
          </div>
          <FormField label={`First week starts (a ${WEEKDAYS[draft.handover_weekday]}) — the first person in the list is on that week`}>
            <input
              className={inputClass}
              type="date"
              value={draft.first_week_start}
              onChange={(e) => setDraft({ ...draft, first_week_start: e.target.value })}
            />
          </FormField>
          <FormField label="Members, in order">
            <div className="space-y-1">
              {draft.member_ids.map((id, i) => {
                const s = byId.get(id);
                return (
                  <div
                    key={id}
                    className="flex items-center gap-2 rounded-md border border-[var(--border-hairline)] px-2 py-1 text-sm text-[var(--text-primary)]"
                  >
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: s?.calendar_colour ?? "#888" }} />
                    <span className="flex-1">
                      {i + 1}. {s ? staffFullName(s) : `#${id}`}
                    </span>
                    <button type="button" className="px-1 text-[var(--text-muted)]" onClick={() => move(i, -1)} title="Up">
                      ↑
                    </button>
                    <button type="button" className="px-1 text-[var(--text-muted)]" onClick={() => move(i, 1)} title="Down">
                      ↓
                    </button>
                    <button
                      type="button"
                      className="px-1 text-[var(--status-critical)]"
                      onClick={() => setDraft({ ...draft, member_ids: draft.member_ids.filter((x) => x !== id) })}
                      title="Remove"
                    >
                      ✕
                    </button>
                  </div>
                );
              })}
              <div className="flex gap-2">
                <select className={inputClass} value={addId} onChange={(e) => setAddId(e.target.value)}>
                  <option value="">+ Add staff member…</option>
                  {staff
                    .filter((s) => !draft.member_ids.includes(s.id))
                    .map((s) => (
                      <option key={s.id} value={s.id}>
                        {staffFullName(s)}
                      </option>
                    ))}
                </select>
                <button
                  type="button"
                  className={btnSecondary}
                  disabled={!addId}
                  onClick={() => {
                    setDraft({ ...draft, member_ids: [...draft.member_ids, Number(addId)] });
                    setAddId("");
                  }}
                >
                  Add
                </button>
              </div>
              {Number(draft.people_per_week) > 1 && (
                <p className="text-xs text-[var(--text-muted)]">
                  Taken {draft.people_per_week} at a time in this order: 1+2, then 3+4, and so on. With a member count that does
                  not divide evenly the pairs mix from week to week.
                </p>
              )}
            </div>
          </FormField>
          <div className="mt-3 flex justify-between">
            <div>
              {draft.id && (
                <button type="button" className={btnSecondary} disabled={busy} onClick={() => remove(draft.id as number)}>
                  Delete rotation
                </button>
              )}
            </div>
            <div className="flex gap-2">
              <button type="button" className={btnSecondary} onClick={() => setDraft(null)}>
                Cancel
              </button>
              <button type="button" className={btnPrimary} disabled={busy || !draft.name.trim()} onClick={save}>
                Save rotation
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div>
          {rotations === null ? (
            <p className="text-sm text-[var(--text-muted)]">Loading…</p>
          ) : (
            <div className="mb-3 space-y-2">
              {rotations.length === 0 && <p className="text-sm text-[var(--text-muted)]">No rotations yet.</p>}
              {rotations.map((r) => (
                <div
                  key={r.id}
                  className="flex items-center justify-between rounded-md border border-[var(--border-hairline)] p-2 text-sm"
                >
                  <div>
                    <div className="font-semibold text-[var(--text-primary)]">
                      {rotationIcon(r.name)} {r.name} {!r.is_active && <span className="text-xs text-[var(--text-muted)]">(off)</span>}
                    </div>
                    <div className="text-xs text-[var(--text-muted)]">
                      {r.people_per_week} per week · {WEEKDAYS[r.handover_weekday]} {r.handover_time.slice(0, 5)} ·{" "}
                      {r.members.map((m) => m.name).join(", ") || "no members"}
                    </div>
                  </div>
                  <button type="button" className={btnSecondary} onClick={() => setDraft(toDraft(r))}>
                    Edit
                  </button>
                </div>
              ))}
            </div>
          )}
          <button type="button" className={btnSecondary} onClick={() => setDraft(toDraft(null))}>
            + New rotation
          </button>

          {weekStarts.length > 0 && rotations && rotations.length > 0 && (
            <div className="mt-4">
              <div className="mb-1 text-xs font-semibold text-[var(--text-secondary)]">Next weeks</div>
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-[var(--text-muted)]">
                    <th className="py-1 pr-2 font-normal">Week of</th>
                    {rotations
                      .filter((r) => r.is_active)
                      .map((r) => (
                        <th key={r.id} className="py-1 pr-2 font-normal">
                          {rotationIcon(r.name)} {r.name}
                        </th>
                      ))}
                  </tr>
                </thead>
                <tbody>
                  {weekStarts.map((ws) => (
                    <tr key={ws} className="border-t border-[var(--border-hairline)]">
                      <td className="py-1 pr-2 text-[var(--text-muted)]">{dmy(ws)}</td>
                      {rotations
                        .filter((r) => r.is_active)
                        .map((r) => {
                          const w = preview.find((x) => x.rotation === r.id && x.week_start === ws);
                          return (
                            <td key={r.id} className="py-1 pr-2 text-[var(--text-primary)]">
                              {w ? w.people.map(personLabel).join(", ") || "—" : "—"}
                            </td>
                          );
                        })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="mt-4 flex justify-end">
            <button type="button" className={btnSecondary} onClick={onClose}>
              Close
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
