import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import { Table, THead, TH, TR, TD } from "./Table";
import { FormField, Modal, btnPrimary, btnSecondary, inputClass } from "./Modal";
import { apiErrorMessage } from "../utils/apiError";
import { formatIsoDate } from "../utils/billingTerms";

/**
 * Configs → Billing → Public holidays (20 Oct 2026). Nobody is suspended on
 * a weekend or on one of these days -- the cut-off moves to the next working
 * day -- except on an agreed extension date. Used to live only in the
 * server's admin site.
 */
type Holiday = { id: number; date: string; name: string; seeded: boolean };

export function PublicHolidaysTab({ onRegisterNewAction }: { onRegisterNewAction: (a: { label: string; onClick: () => void }[] | null) => void }) {
  const thisYear = new Date().getFullYear();
  const [year, setYear] = useState(thisYear);
  const [rows, setRows] = useState<Holiday[] | null>(null);
  const [editing, setEditing] = useState<Partial<Holiday> | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function load() {
    api
      .get<Holiday[]>(`/public-holidays/?year=${year}`)
      .then((r) => setRows(r.data))
      .catch(() => setRows([]));
  }

  useEffect(() => {
    onRegisterNewAction([{ label: "+ Add holiday", onClick: () => { setError(""); setEditing({ date: "", name: "" }); } }]);
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [year]);

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!editing) return;
    setBusy(true);
    setError("");
    try {
      const body = { date: editing.date, name: editing.name };
      if (editing.id) await api.patch(`/public-holidays/${editing.id}/`, body);
      else await api.post("/public-holidays/", body);
      setEditing(null);
      load();
    } catch (err) {
      setError(apiErrorMessage(err, "Couldn't save the holiday."));
    } finally {
      setBusy(false);
    }
  }

  async function remove(h: Holiday) {
    if (!window.confirm(`Remove ${h.name} (${formatIsoDate(h.date)})? Clients could then be suspended on that day.`)) return;
    try {
      await api.delete(`/public-holidays/${h.id}/`);
      load();
    } catch (err) {
      window.alert(apiErrorMessage(err, "Couldn't remove it."));
    }
  }

  const weekday = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-ZA", { weekday: "long" });

  return (
    <div>
      <p className="mb-3 max-w-2xl text-sm text-[var(--text-secondary)]">
        Nobody is suspended on a weekend or on these days — the cut-off moves to the next working day. An agreed
        extension date is the one exception. Invoices still go out on their billing date.
      </p>
      <div className="mb-3 flex items-center gap-2 text-sm">
        <button type="button" className={btnSecondary} onClick={() => setYear(year - 1)}>←</button>
        <span className="w-14 text-center font-medium tabular-nums">{year}</span>
        <button type="button" className={btnSecondary} onClick={() => setYear(year + 1)}>→</button>
      </div>
      {rows && rows.length === 0 ? (
        <p className="text-sm text-[var(--status-warning)]">
          No public holidays recorded for {year} — clients can be suspended on any weekday. Add them with + Add holiday.
        </p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Date</TH>
              <TH>Day</TH>
              <TH>Holiday</TH>
              <TH>Added</TH>
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {(rows ?? []).map((h) => (
              <TR key={h.id}>
                <TD className="tabular-nums">{formatIsoDate(h.date)}</TD>
                <TD>{weekday(h.date)}</TD>
                <TD>{h.name}</TD>
                <TD className="text-xs text-[var(--text-muted)]">{h.seeded ? "Standard list" : "By hand"}</TD>
                <TD>
                  <div className="flex gap-3">
                    <button className="text-[var(--series-1)] hover:underline" onClick={() => { setError(""); setEditing(h); }}>
                      Edit
                    </button>
                    <button className="text-red-600 hover:underline dark:text-red-400" onClick={() => remove(h)}>
                      Remove
                    </button>
                  </div>
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {editing && (
        <Modal title={editing.id ? "Edit public holiday" : "Add public holiday"} onClose={() => setEditing(null)}>
          <form onSubmit={save}>
            <FormField label="Date" required>
              <input type="date" required className={`${inputClass} w-auto`} value={editing.date ?? ""}
                onChange={(e) => setEditing({ ...editing, date: e.target.value })} />
            </FormField>
            <FormField label="Name" required>
              <input required className={inputClass} value={editing.name ?? ""} placeholder="e.g. Heritage Day"
                onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
            </FormField>
            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setEditing(null)}>Cancel</button>
              <button type="submit" className={btnPrimary} disabled={busy}>{busy ? "Saving…" : "Save"}</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
