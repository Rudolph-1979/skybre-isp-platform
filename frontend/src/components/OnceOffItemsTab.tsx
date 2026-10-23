import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import { useApiList } from "../hooks/useApiList";
import { Table, THead, TH, SortableTH, TR, TD } from "./Table";
import { StatusBadge } from "./StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "./Modal";
import type { OnceOffItem } from "../types";
import { apiErrorMessage } from "../utils/apiError";

// The list of standard once-off charges -- Installation, Relocation, 6 m
// pole, Call-out (Rudolph, 6 Oct 2026: "Once off invoice line items should
// be creatable"). Picked as an "Once-off item" line on New invoice / New
// quote, which fills the name, price and VAT. Sits beside Recurring Charges,
// same Configs permission. Changing a price here never changes an invoice
// already made -- each line keeps its own copy.
type EditForm = { name: string; price: string; tax_rate_pct: string; description: string; is_active: boolean };

const EMPTY: EditForm = { name: "", price: "", tax_rate_pct: "15", description: "", is_active: true };

export function OnceOffItemsTab() {
  const [ordering, setOrdering] = useState("name");
  const { items, loading, refetch } = useApiList<OnceOffItem>(
    `/once-off-items/?page_size=100&ordering=${ordering}`
  );
  const [editing, setEditing] = useState<OnceOffItem | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState<EditForm>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggleSort(field: string) {
    setOrdering((prev) => (prev === field ? `-${field}` : prev === `-${field}` ? "name" : field));
  }

  function openNew() {
    setEditing(null);
    setForm(EMPTY);
    setError(null);
    setShowModal(true);
  }

  function openEdit(charge: OnceOffItem) {
    setEditing(charge);
    setForm({
      name: charge.name,
      price: charge.price,
      tax_rate_pct: charge.tax_rate_pct,
      description: charge.description,
      is_active: charge.is_active,
    });
    setError(null);
    setShowModal(true);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      if (editing) {
        await api.patch(`/once-off-items/${editing.id}/`, form);
      } else {
        await api.post("/once-off-items/", form);
      }
      setShowModal(false);
      refetch();
    } catch (err: any) {
      setError(apiErrorMessage(err, "Couldn't save this item."));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(charge: OnceOffItem) {
    if (!confirm(`Delete "${charge.name}"? This can't be undone.`)) return;
    try {
      await api.delete(`/once-off-items/${charge.id}/`);
      refetch();
    } catch (err: any) {
      alert(apiErrorMessage(err, "Couldn't delete this item."));
    }
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-[var(--text-muted)]">
          Standard once-off charges — installation, relocation, poles, call-outs. Pick one as a
          "Once-off item" line on a new invoice or quote and its name, price and VAT fill in. Prices are excl. VAT.
        </p>
        <button className={btnPrimary} onClick={openNew}>
          + New item
        </button>
      </div>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : items.length === 0 ? (
        <p className="text-[var(--text-muted)]">No once-off items yet.</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <SortableTH field="name" ordering={ordering} onSort={toggleSort}>Name</SortableTH>
              <SortableTH field="price" ordering={ordering} onSort={toggleSort}>Price</SortableTH>
              <TH>Tax %</TH>
              <TH>Used</TH>
              <SortableTH field="is_active" ordering={ordering} onSort={toggleSort}>Status</SortableTH>
              <TH />
            </tr>
          </THead>
          <tbody>
            {items.map((c) => (
              <TR key={c.id}>
                <TD className="font-medium">
                  <button type="button" className="hover:underline" onClick={() => openEdit(c)}>
                    {c.name}
                  </button>
                </TD>
                <TD className="tabular-nums">R {parseFloat(c.price).toFixed(2)}</TD>
                <TD>{c.tax_rate_pct}%</TD>
                <TD>{c.used_count}</TD>
                <TD><StatusBadge status={c.is_active ? "active" : "inactive"} /></TD>
                <TD>
                  <button
                    type="button"
                    className="text-xs text-red-700 hover:underline dark:text-red-300"
                    onClick={() => handleDelete(c)}
                  >
                    Delete
                  </button>
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title={editing ? `Edit ${editing.name}` : "New once-off item"} onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Name">
              <input
                className={inputClass}
                required
                placeholder="Installation"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </FormField>
            <FormField label="Price (R, excl. VAT)">
              <input
                type="number"
                step="0.01"
                className={inputClass}
                required
                value={form.price}
                onChange={(e) => setForm({ ...form, price: e.target.value })}
              />
            </FormField>
            <FormField label="Tax rate (%)">
              <input
                type="number"
                step="0.01"
                className={inputClass}
                required
                value={form.tax_rate_pct}
                onChange={(e) => setForm({ ...form, tax_rate_pct: e.target.value })}
              />
            </FormField>
            <FormField label="Description">
              <textarea
                className={inputClass}
                rows={2}
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
            </FormField>
            <label className="mt-2 flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.is_active}
                onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
              />
              Active — offered on New invoice / New quote
            </label>
            {!form.is_active && editing && (
              <p className="mt-2 text-xs text-[var(--text-muted)]">
                Invoices and quotes that already have it are not changed. Unticking just takes it off the
                list for new ones.
              </p>
            )}

            {error && <p className="mt-3 text-sm text-red-700 dark:text-red-300">{error}</p>}

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : editing ? "Save changes" : "Create item"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
