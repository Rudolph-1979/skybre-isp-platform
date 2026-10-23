import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import { useApiList } from "../hooks/useApiList";
import { Table, THead, TH, SortableTH, TR, TD } from "./Table";
import { StatusBadge } from "./StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "./Modal";
import type { RecurringCharge } from "../types";

// The catalog of standard recurring add-ons -- IP Rental, Domain Rental,
// etc (Rudolph, 30 Sep: "In tariffs there need to be a option to add
// standard recurring billing like IP rental, Domain Rental etc"). Its own
// tab on the Tariffs page, same section/permission as Tariffs itself
// (Configs), because it is the same kind of decision: what gets charged,
// and how much. Given to a client from their page's "+ Add recurring
// service" button (CustomerRecurringCharge).
type EditForm = { name: string; price: string; tax_rate_pct: string; description: string; is_active: boolean };

const EMPTY: EditForm = { name: "", price: "", tax_rate_pct: "15", description: "", is_active: true };

export function RecurringChargesTab() {
  const [ordering, setOrdering] = useState("name");
  const { items, loading, refetch } = useApiList<RecurringCharge>(
    `/recurring-charges/?page_size=100&ordering=${ordering}`
  );
  const [editing, setEditing] = useState<RecurringCharge | null>(null);
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

  function openEdit(charge: RecurringCharge) {
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
        await api.patch(`/recurring-charges/${editing.id}/`, form);
      } else {
        await api.post("/recurring-charges/", form);
      }
      setShowModal(false);
      refetch();
    } catch (err: any) {
      setError(err?.response?.data?.name?.[0] || err?.response?.data?.detail || "Couldn't save this charge.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(charge: RecurringCharge) {
    if (!confirm(`Delete "${charge.name}"? This can't be undone.`)) return;
    try {
      await api.delete(`/recurring-charges/${charge.id}/`);
      refetch();
    } catch (err: any) {
      alert(err?.response?.data?.detail || "Couldn't delete this charge.");
    }
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-[var(--text-muted)]">
          Standard recurring charges staff can add to a client -- IP rental, domain, email, web hosting, and
          the like. Each one bills as its own line on the client's invoice, with or without a service.
        </p>
        <button className={btnPrimary} onClick={openNew}>
          + New charge
        </button>
      </div>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : items.length === 0 ? (
        <p className="text-[var(--text-muted)]">No recurring charges set up yet.</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <SortableTH field="name" ordering={ordering} onSort={toggleSort}>Name</SortableTH>
              <SortableTH field="price" ordering={ordering} onSort={toggleSort}>Price</SortableTH>
              <TH>Tax %</TH>
              <TH>Clients</TH>
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
                  {c.partner_name && (
                    <span className="ml-2 text-xs text-[var(--text-muted)]">({c.partner_name} only)</span>
                  )}
                </TD>
                <TD className="tabular-nums">R {parseFloat(c.price).toFixed(2)}</TD>
                <TD>{c.tax_rate_pct}%</TD>
                <TD>{c.client_count}</TD>
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
        <Modal title={editing ? `Edit ${editing.name}` : "New recurring charge"} onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Name">
              <input
                className={inputClass}
                required
                placeholder="IP Rental"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </FormField>
            <FormField label="Price (R)">
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
              Active — offered when adding a recurring service to a client
            </label>
            {!form.is_active && editing && (
              <p className="mt-2 text-xs text-[var(--text-muted)]">
                Clients already carrying this charge keep being billed for it. Untick just takes it off
                the list for newly adding it to a client.
              </p>
            )}

            {error && <p className="mt-3 text-sm text-red-700 dark:text-red-300">{error}</p>}

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : editing ? "Save changes" : "Create charge"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
