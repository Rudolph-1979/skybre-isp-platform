import { useState } from "react";
import { api } from "../api/client";
import { apiErrorMessage } from "../utils/apiError";
import { btnPrimary, btnSecondary, inputClass } from "./Modal";

/**
 * Overview tile: free-typed equipment at the client -- router, PtP radio,
 * CPE, IPs, logins, Wi-Fi (Rudolph 20 Oct 2026: "Clean Typeable information
 * tile"). Plain text, nothing linked. Staff only; never shown to the client.
 */
export function EquipmentDetailsTile({
  customerId,
  value,
  onSaved,
}: {
  customerId: number;
  value: string;
  onSaved: (value: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    setSaving(true);
    setError("");
    try {
      await api.patch(`/customers/${customerId}/`, { equipment_details: draft });
      onSaved(draft);
      setEditing(false);
    } catch (err) {
      setError(apiErrorMessage(err, "Couldn't save."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 sm:col-span-2">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[var(--text-muted)]">Equipment &amp; router details</p>
        {!editing && (
          <button
            type="button"
            className="text-xs text-[var(--series-1)] hover:underline"
            onClick={() => {
              setDraft(value);
              setError("");
              setEditing(true);
            }}
          >
            {value.trim() ? "Edit" : "Add"}
          </button>
        )}
      </div>
      {editing ? (
        <div className="mt-2">
          <textarea
            className={`${inputClass} font-mono text-xs`}
            rows={6}
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={"e.g.\nPtP: LiteBeam 5AC Gen2 — 10.20.1.15 — admin / ****\nRouter: hAP ac² — SN ABC123 — Wi-Fi: Visagie-Home / ****"}
          />
          {error && <p className="mt-1 text-xs text-[var(--status-critical)]">{error}</p>}
          <div className="mt-2 flex justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={() => setEditing(false)}>
              Cancel
            </button>
            <button type="button" className={btnPrimary} disabled={saving} onClick={save}>
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      ) : value.trim() ? (
        <p className="mt-1 whitespace-pre-wrap break-words font-mono text-xs text-[var(--text-primary)]">{value}</p>
      ) : (
        <p className="mt-1 text-[var(--text-muted)]">Nothing recorded yet</p>
      )}
    </div>
  );
}
