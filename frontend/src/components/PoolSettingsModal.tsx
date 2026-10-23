import { useEffect, useState } from "react";
import { api } from "../api/client";
import { apiErrorMessage } from "../utils/apiError";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "./Modal";

type Settings = { auto_return_enabled: boolean; auto_return_time: string; last_auto_return_on: string | null };

// The daily return of uncompleted jobs to the pool (Rudolph, 6 Oct 2026:
// "uncompleted jobs should return to the pool as well daily on a editable
// time"). scheduling.SchedulingSettings / return_jobs_to_pool.
export function PoolSettingsModal({ onClose }: { onClose: () => void }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get<Settings>("/scheduling-settings/").then((res) => setSettings(res.data)).catch(() => setError("Couldn't load the settings."));
  }, []);

  async function save() {
    if (!settings) return;
    setSaving(true);
    setError("");
    try {
      await api.patch("/scheduling-settings/", {
        auto_return_enabled: settings.auto_return_enabled,
        auto_return_time: settings.auto_return_time.slice(0, 5),
      });
      onClose();
    } catch (err) {
      setError(apiErrorMessage(err, "Couldn't save the settings."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal title="Pool settings" onClose={onClose}>
      {!settings ? (
        <p className="text-sm text-[var(--text-muted)]">{error || "Loading…"}</p>
      ) : (
        <>
          <label className="mb-3 flex items-center gap-2 text-sm text-[var(--text-primary)]">
            <input
              type="checkbox"
              checked={settings.auto_return_enabled}
              onChange={(e) => setSettings({ ...settings, auto_return_enabled: e.target.checked })}
            />
            Every day, send jobs nobody completed back to the pool
          </label>
          <FormField label="At">
            <input
              type="time"
              className={`${inputClass} max-w-[10rem]`}
              disabled={!settings.auto_return_enabled}
              value={settings.auto_return_time.slice(0, 5)}
              onChange={(e) => setSettings({ ...settings, auto_return_time: e.target.value })}
            />
          </FormField>
          <p className="mb-3 text-xs text-[var(--text-muted)]">
            At this time, every booked job whose time is over and isn't Completed (or Cancelled) loses its time and
            technicians, comes off their phones, and goes back to the pool marked "Not completed — returned
            automatically". Jobs later in the day are left alone. Technicians can also press Return in the app to send
            a job back straight away.
            {settings.last_auto_return_on ? ` Last run: ${settings.last_auto_return_on}.` : ""}
          </p>
          {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
          <div className="flex justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={onClose}>Cancel</button>
            <button type="button" className={btnPrimary} disabled={saving} onClick={save}>
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </>
      )}
    </Modal>
  );
}
