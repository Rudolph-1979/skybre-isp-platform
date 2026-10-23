import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import { FormField, btnPrimary, inputClass } from "./Modal";
import { apiErrorMessage } from "../utils/apiError";

/**
 * Configs → Support (#212, Admin only): what the Support page measures
 * against. The defaults are a starting point to be confirmed.
 */
type SupportSettings = {
  reply_target_minutes: number;
  office_start: string;
  office_end: string;
  office_days: number[];
  outage_min_clients: number;
  outage_window_minutes: number;
};

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function SupportSettingsTab() {
  const [form, setForm] = useState<SupportSettings | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get<SupportSettings>("/support/settings/")
      .then((r) => setForm({ ...r.data, office_start: r.data.office_start.slice(0, 5), office_end: r.data.office_end.slice(0, 5) }))
      .catch(() => setError("Could not load the support settings."));
  }, []);

  function set<K extends keyof SupportSettings>(key: K, value: SupportSettings[K]) {
    setSaved(false);
    setForm((f) => (f ? { ...f, [key]: value } : f));
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!form) return;
    setBusy(true);
    setError("");
    try {
      await api.patch("/support/settings/", form);
      setSaved(true);
    } catch (err) {
      setError(apiErrorMessage(err, "Couldn't save the support settings."));
    } finally {
      setBusy(false);
    }
  }

  if (!form) return <p className="text-sm text-[var(--text-muted)]">{error || "Loading…"}</p>;

  return (
    <form onSubmit={save} className="max-w-2xl space-y-4">
      <p className="text-sm text-[var(--text-secondary)]">
        What the Support page measures against. These started as defaults (first reply within an hour, 08:00–17:00
        Monday to Friday, 5 clients in 15 minutes) — confirm them or change them here. Response times only count
        office time, and skip the public holidays under Billing → Public holidays.
      </p>

      <FormField label="First reply target (minutes of office time)">
        <input
          type="number" min={1} max={10080} required className={inputClass}
          value={form.reply_target_minutes}
          onChange={(e) => set("reply_target_minutes", Number(e.target.value))}
        />
      </FormField>

      <div className="grid gap-4 sm:grid-cols-2">
        <FormField label="Office opens">
          <input type="time" required className={inputClass} value={form.office_start} onChange={(e) => set("office_start", e.target.value)} />
        </FormField>
        <FormField label="Office closes">
          <input type="time" required className={inputClass} value={form.office_end} onChange={(e) => set("office_end", e.target.value)} />
        </FormField>
      </div>

      <fieldset>
        <legend className="mb-1 text-sm font-medium text-[var(--text-secondary)]">Office days</legend>
        <div className="flex flex-wrap gap-3">
          {DAYS.map((d, i) => (
            <label key={d} className="flex items-center gap-1.5 text-sm text-[var(--text-primary)]">
              <input
                type="checkbox"
                checked={form.office_days.includes(i)}
                onChange={(e) =>
                  set("office_days", e.target.checked ? [...form.office_days, i].sort() : form.office_days.filter((x) => x !== i))
                }
              />
              {d}
            </label>
          ))}
        </div>
      </fieldset>

      <div className="grid gap-4 sm:grid-cols-2">
        <FormField label="Possible outage: at least this many clients" hint="On one access point / sector, or one router when that isn't filled in.">
          <input
            type="number" min={2} required className={inputClass}
            value={form.outage_min_clients}
            onChange={(e) => set("outage_min_clients", Number(e.target.value))}
          />
        </FormField>
        <FormField label="…going offline within (minutes)">
          <input
            type="number" min={1} max={240} required className={inputClass}
            value={form.outage_window_minutes}
            onChange={(e) => set("outage_window_minutes", Number(e.target.value))}
          />
        </FormField>
      </div>

      {error && <p className="text-sm text-[var(--status-critical)]">{error}</p>}
      <div className="flex items-center gap-3">
        <button type="submit" disabled={busy} className={btnPrimary}>{busy ? "Saving…" : "Save"}</button>
        {saved && <span className="text-sm text-[var(--status-good)]">Saved.</span>}
      </div>
    </form>
  );
}
