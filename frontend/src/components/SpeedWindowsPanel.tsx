import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import { useApiList } from "../hooks/useApiList";
import { Table, THead, TH, TR, TD } from "./Table";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "./Modal";
import { WEEKDAY_LABELS } from "../types";
import type { Paginated, SpeedWindow, Tariff } from "../types";

/**
 * Time-of-day speed windows.
 *
 * The boost is a PERCENTAGE of the plan rather than an absolute speed, so
 * one window covers every tariff — 200% doubles a 10 Mbps line and a
 * 50 Mbps line, and neither needs its own row. The table shows what that
 * works out to for a real plan, because a percentage alone is not a
 * number anybody can quote down the phone.
 */

const EMPTY = {
  name: "",
  tariff: "",
  start_time: "22:00",
  end_time: "06:00",
  weekdays: [] as number[],
  speed_pct: 200,
  counts_toward_fup: false,
  is_active: true,
};

function describeDays(days: number[]) {
  if (!days.length) return "Every day";
  if (days.length === 7) return "Every day";
  return days
    .slice()
    .sort((a, b) => a - b)
    .map((d) => WEEKDAY_LABELS[d])
    .join(", ");
}

export function SpeedWindowsPanel() {
  const [windows, setWindows] = useState<SpeedWindow[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<SpeedWindow | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const { items: tariffs } = useApiList<Tariff>("/tariffs/?page_size=200");

  const load = useCallback(() => {
    setLoading(true);
    api
      .get<Paginated<SpeedWindow>>("/speed-windows/?page_size=100")
      .then((res) => setWindows(res.data.results))
      .catch(() => setError("Couldn't load the speed windows."))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  function openNew() {
    setEditing(null);
    setForm(EMPTY);
    setError("");
    setShowModal(true);
  }

  function openEdit(w: SpeedWindow) {
    setEditing(w);
    setForm({
      name: w.name,
      tariff: w.tariff ? String(w.tariff) : "",
      start_time: w.start_time.slice(0, 5),
      end_time: w.end_time.slice(0, 5),
      weekdays: w.weekdays ?? [],
      speed_pct: w.speed_pct,
      counts_toward_fup: w.counts_toward_fup,
      is_active: w.is_active,
    });
    setError("");
    setShowModal(true);
  }

  async function handleSave(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    const payload = { ...form, tariff: form.tariff ? Number(form.tariff) : null };
    try {
      if (editing) await api.patch(`/speed-windows/${editing.id}/`, payload);
      else await api.post("/speed-windows/", payload);
      setShowModal(false);
      load();
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, string[]> } })?.response?.data;
      const first = data ? Object.values(data)[0] : null;
      setError(Array.isArray(first) ? first[0] : "Couldn't save that window.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(w: SpeedWindow) {
    if (!window.confirm(`Delete the “${w.name}” window? Lines go back to their plan speed during those hours.`)) {
      return;
    }
    await api.delete(`/speed-windows/${w.id}/`);
    load();
  }

  // A worked example beats a percentage. Uses the slowest real plan so the
  // number shown is the least impressive one, not the most.
  const sample = tariffs
    .filter((t) => t.speed_download_kbps)
    .sort((a, b) => (a.speed_download_kbps ?? 0) - (b.speed_download_kbps ?? 0))[0];

  return (
    <div>
      <div className="mb-3 flex items-start justify-between gap-3">
        <p className="max-w-2xl text-xs text-[var(--text-muted)]">
          Hours when lines run faster than their plan. The point is load, not generosity: your
          evenings are full and your small hours are empty, so speed given away at 02:00 costs
          nothing that's being used and moves the big downloads off the times everyone else is
          trying to work.
        </p>
        <button className={btnPrimary} onClick={openNew}>
          + New window
        </button>
      </div>

      {error && !showModal && (
        <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>
      )}

      {loading && windows.length === 0 ? (
        <p className="py-6 text-sm text-[var(--text-muted)]">Loading…</p>
      ) : windows.length === 0 ? (
        <p className="py-6 text-sm text-[var(--text-muted)]">
          No windows yet — every line runs at its plan speed around the clock.
        </p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Window</TH>
              <TH>When</TH>
              <TH>Days</TH>
              <TH>Speed</TH>
              <TH>Applies to</TH>
              <TH>Counts to fair use</TH>
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {windows.map((w) => (
              <TR key={w.id}>
                <TD>
                  <span className={w.is_active ? "font-medium" : "font-medium line-through opacity-60"}>
                    {w.name}
                  </span>
                  {!w.is_active && <span className="ml-2 text-xs text-[var(--text-muted)]">off</span>}
                </TD>
                <TD className="tabular-nums">
                  {w.start_time.slice(0, 5)} – {w.end_time.slice(0, 5)}
                  {/* Stated rather than left to be worked out: a window
                      from 22:00 to 06:00 looks like a mistake until you
                      realise it runs through the night. */}
                  {w.spans_midnight && (
                    <span className="block text-xs text-[var(--text-muted)]">through midnight</span>
                  )}
                </TD>
                <TD>{describeDays(w.weekdays ?? [])}</TD>
                <TD className="tabular-nums">
                  {w.speed_pct}%
                  {sample && sample.speed_download_kbps && (
                    <span className="block text-xs text-[var(--text-muted)]">
                      {sample.name}: {Math.round((sample.speed_download_kbps * w.speed_pct) / 100 / 1024)} Mbps
                    </span>
                  )}
                </TD>
                <TD>{w.tariff_name || "Every plan"}</TD>
                <TD>
                  {w.counts_toward_fup ? (
                    <span className="text-[var(--status-warning)]">Yes</span>
                  ) : (
                    <span className="text-[var(--text-muted)]">No</span>
                  )}
                </TD>
                <TD>
                  <div className="flex gap-3">
                    <button className="text-[var(--series-1)] hover:underline" onClick={() => openEdit(w)}>
                      Edit
                    </button>
                    <button
                      className="text-red-600 hover:underline dark:text-red-400"
                      onClick={() => handleDelete(w)}
                    >
                      Delete
                    </button>
                  </div>
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title={editing ? `Edit ${editing.name}` : "New speed window"} onClose={() => setShowModal(false)}>
          <form onSubmit={handleSave}>
            <FormField label="Name" required>
              <input
                className={inputClass}
                required
                placeholder="Night burst"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </FormField>
            <div className="grid grid-cols-2 gap-3">
              <FormField label="From" required>
                <input
                  type="time"
                  className={inputClass}
                  required
                  value={form.start_time}
                  onChange={(e) => setForm({ ...form, start_time: e.target.value })}
                />
              </FormField>
              <FormField
                label="To"
                required
                hint={form.start_time > form.end_time ? "Runs through midnight." : undefined}
              >
                <input
                  type="time"
                  className={inputClass}
                  required
                  value={form.end_time}
                  onChange={(e) => setForm({ ...form, end_time: e.target.value })}
                />
              </FormField>
            </div>

            <FormField label="Days" hint="None selected = every day.">
              <div className="flex flex-wrap gap-1">
                {WEEKDAY_LABELS.map((label, index) => {
                  const on = form.weekdays.includes(index);
                  return (
                    <button
                      key={label}
                      type="button"
                      onClick={() =>
                        setForm({
                          ...form,
                          weekdays: on
                            ? form.weekdays.filter((d) => d !== index)
                            : [...form.weekdays, index],
                        })
                      }
                      className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                        on
                          ? "bg-[var(--series-1)] text-white"
                          : "bg-[var(--surface-2)] text-[var(--text-secondary)] ring-1 ring-[var(--border-hairline)]"
                      }`}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
            </FormField>

            <FormField
              label="Speed while it's on (% of plan)"
              required
              hint={
                sample && sample.speed_download_kbps
                  ? `${sample.name} would run at ${Math.round(
                      (sample.speed_download_kbps * (form.speed_pct || 0)) / 100 / 1024
                    )} Mbps.`
                  : "200 = double the plan speed."
              }
            >
              <input
                type="number"
                min={1}
                max={1000}
                className={inputClass}
                required
                value={form.speed_pct}
                onChange={(e) => setForm({ ...form, speed_pct: Number(e.target.value) })}
              />
            </FormField>

            <FormField label="Applies to">
              <select
                className={inputClass}
                value={form.tariff}
                onChange={(e) => setForm({ ...form, tariff: e.target.value })}
              >
                <option value="">Every plan</option>
                {tariffs.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </FormField>

            <label className="mb-3 flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={form.counts_toward_fup}
                onChange={(e) => setForm({ ...form, counts_toward_fup: e.target.checked })}
              />
              <span>
                <span className="font-medium text-[var(--text-secondary)]">
                  Traffic in this window counts toward fair use
                </span>
                <span className="mt-0.5 block text-xs text-[var(--text-muted)]">
                  Usually leave this off. A window whose traffic still counts gives nobody a
                  reason to move their downloads into it — which is the whole point of having one.
                </span>
              </span>
            </label>

            <label className="mb-3 flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.is_active}
                onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
              />
              <span className="font-medium text-[var(--text-secondary)]">Active</span>
            </label>

            {error && <p className="mt-1 text-sm text-[var(--status-critical)]">{error}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" className={btnPrimary} disabled={saving}>
                {saving ? "Saving…" : "Save window"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
