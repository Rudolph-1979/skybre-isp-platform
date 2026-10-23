import { useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api/client";
import { apiErrorMessage } from "../utils/apiError";
import { FormField, Modal, inputClass, btnPrimary, btnSecondary } from "./Modal";

// Paying / Sponsor / Deal / Staff ... (Rudolph, 6 Oct 2026) -- a label with
// its reason, so anyone looking at a cheap or free account can see why. It
// doesn't change billing; the money side stays on each service's discount.
// The list is editable ("Add a option to add more options"): see
// customers.ClientAccountType on the backend, /api/account-types/.
export type AccountTypeOption = {
  id: number;
  key: string;
  name: string;
  needs_reason: boolean;
  colour: string;
  is_active: boolean;
  sort_order: number;
  customer_count: number;
};

const FALLBACK = { name: "Paying", colour: "#1a9e6e" };

export function AccountTypeBadge({ type, types }: { type: string | undefined | null; types: AccountTypeOption[] }) {
  const key = type || "paying";
  const option = types.find((t) => t.key === key);
  const name = option?.name ?? (key === "paying" ? FALLBACK.name : key.replace(/_/g, " "));
  const colour = option?.colour ?? FALLBACK.colour;
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
      style={{ background: `${colour}26`, color: colour }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: colour }} />
      {name}
    </span>
  );
}

export function AccountTypeFields({
  type,
  reason,
  types,
  onChange,
  onTypesChanged,
}: {
  type: string | undefined | null;
  reason: string | undefined | null;
  types: AccountTypeOption[];
  onChange: (type: string, reason: string) => void;
  onTypesChanged: () => void;
}) {
  const [managing, setManaging] = useState(false);
  const key = type || "paying";
  const option = types.find((t) => t.key === key);
  const choices = types.filter((t) => t.is_active || t.key === key);
  const askReason = key !== "paying" && (option?.needs_reason || !!reason);
  return (
    <>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[12rem_1fr]">
        <FormField label="Account">
          <select className={inputClass} value={key} onChange={(e) => onChange(e.target.value, reason ?? "")}>
            {choices.map((t) => (
              <option key={t.key} value={t.key}>
                {t.name}
                {t.is_active ? "" : " (switched off)"}
              </option>
            ))}
            {!option && <option value={key}>{key}</option>}
          </select>
          <button
            type="button"
            className="mt-1 text-xs text-[var(--series-1)] hover:underline"
            onClick={() => setManaging(true)}
          >
            Edit list…
          </button>
        </FormField>
        {(askReason || (key !== "paying" && option && !option.needs_reason)) && (
          <FormField label={option?.needs_reason ? `Why ${option?.name ?? "this type"}?` : "Note (optional)"}>
            <input
              className={inputClass}
              required={!!option?.needs_reason}
              maxLength={255}
              placeholder={
                key === "sponsor"
                  ? "e.g. Rugby club sponsorship, free 20 Mbps"
                  : key === "deal"
                    ? "e.g. Half price for 3 months, launch special"
                    : key === "staff"
                      ? "e.g. Technician, staff rate"
                      : ""
              }
              value={reason ?? ""}
              onChange={(e) => onChange(key, e.target.value)}
            />
          </FormField>
        )}
      </div>
      {/* Portalled out of the client form, and no <form> of its own: it
          is opened from inside New/Edit client, and a nested form (or a
          submit bubbling up the React tree) would save the client. */}
      {managing &&
        createPortal(
          <AccountTypesModal types={types} onChanged={onTypesChanged} onClose={() => setManaging(false)} />,
          document.body,
        )}
    </>
  );
}

export function AccountTypesModal({
  types,
  onChanged,
  onClose,
}: {
  types: AccountTypeOption[];
  onChanged: () => void;
  onClose: () => void;
}) {
  const [drafts, setDrafts] = useState<Record<number, { name: string; colour: string; needs_reason: boolean }>>({});
  const [newName, setNewName] = useState("");
  const [newColour, setNewColour] = useState("#d0661f");
  const [newNeedsReason, setNewNeedsReason] = useState(true);
  const [busy, setBusy] = useState<number | "new" | null>(null);
  const [error, setError] = useState("");

  const draftOf = (t: AccountTypeOption) =>
    drafts[t.id] ?? { name: t.name, colour: t.colour, needs_reason: t.needs_reason };

  async function run(id: number | "new", fn: () => Promise<unknown>) {
    setBusy(id);
    setError("");
    try {
      await fn();
      onChanged();
      return true;
    } catch (err) {
      setError(apiErrorMessage(err, "Couldn't save that."));
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function add() {
    if (!newName.trim()) return;
    const ok = await run("new", () =>
      api.post("/account-types/", { name: newName, colour: newColour, needs_reason: newNeedsReason }),
    );
    if (ok) setNewName("");
  }

  return (
    <Modal title="Account types" onClose={onClose}>
      <p className="mb-3 text-sm text-[var(--text-secondary)]">
        The options for a client's Account. Tick "Ask why" to make staff give a reason when they pick it. Renaming
        relabels every client on it. A type in use can't be deleted — switch it off instead. Paying is the default
        and always stays.
      </p>
      <div className="mb-4 space-y-2">
        {types.map((t) => {
          const d = draftOf(t);
          const dirty = d.name !== t.name || d.colour !== t.colour || d.needs_reason !== t.needs_reason;
          const paying = t.key === "paying";
          return (
            <div key={t.id} className={`flex flex-wrap items-center gap-2 ${t.is_active ? "" : "opacity-60"}`}>
              <input
                type="color"
                className="h-9 w-10 cursor-pointer rounded border border-[var(--border-hairline)] bg-transparent"
                value={d.colour}
                onChange={(e) => setDrafts({ ...drafts, [t.id]: { ...d, colour: e.target.value } })}
                title="Badge colour"
              />
              <input
                className={`${inputClass} min-w-0 flex-1`}
                value={d.name}
                onChange={(e) => setDrafts({ ...drafts, [t.id]: { ...d, name: e.target.value } })}
              />
              <label className="flex items-center gap-1 text-xs text-[var(--text-secondary)]" title="Staff must give a reason">
                <input
                  type="checkbox"
                  disabled={paying}
                  checked={d.needs_reason}
                  onChange={(e) => setDrafts({ ...drafts, [t.id]: { ...d, needs_reason: e.target.checked } })}
                />
                Ask why
              </label>
              <span className="w-16 text-right text-xs text-[var(--text-muted)]">
                {t.customer_count} client{t.customer_count === 1 ? "" : "s"}
              </span>
              {dirty && (
                <button type="button" className={btnPrimary} disabled={busy === t.id} onClick={() =>
                  run(t.id, () => api.patch(`/account-types/${t.id}/`, d)).then((ok) => {
                    if (ok) setDrafts(({ [t.id]: _gone, ...rest }) => rest);
                  })
                }>
                  Save
                </button>
              )}
              <label className="flex items-center gap-1 text-xs text-[var(--text-secondary)]">
                <input
                  type="checkbox"
                  disabled={paying || busy === t.id}
                  checked={t.is_active}
                  onChange={(e) => run(t.id, () => api.patch(`/account-types/${t.id}/`, { is_active: e.target.checked }))}
                />
                On
              </label>
              {!paying && t.customer_count === 0 && (
                <button
                  type="button"
                  className="text-xs text-[var(--status-critical)] hover:underline"
                  disabled={busy === t.id}
                  onClick={() => run(t.id, () => api.delete(`/account-types/${t.id}/`))}
                >
                  Delete
                </button>
              )}
            </div>
          );
        })}
      </div>
      <div className="flex flex-wrap items-center gap-2 border-t border-[var(--border-hairline)] pt-4">
        <input
          type="color"
          className="h-9 w-10 cursor-pointer rounded border border-[var(--border-hairline)] bg-transparent"
          value={newColour}
          onChange={(e) => setNewColour(e.target.value)}
          title="Badge colour"
        />
        <input
          className={`${inputClass} min-w-0 flex-1`}
          placeholder="New account type, e.g. Family"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
        />
        <label className="flex items-center gap-1 text-xs text-[var(--text-secondary)]">
          <input type="checkbox" checked={newNeedsReason} onChange={(e) => setNewNeedsReason(e.target.checked)} />
          Ask why
        </label>
        <button type="button" className={btnPrimary} disabled={busy === "new" || !newName.trim()} onClick={add}>
          + Add
        </button>
      </div>
      {error && <p className="mt-3 text-sm text-[var(--status-critical)]">{error}</p>}
      <div className="mt-4 flex justify-end">
        <button type="button" className={btnSecondary} onClick={onClose}>
          Done
        </button>
      </div>
    </Modal>
  );
}
