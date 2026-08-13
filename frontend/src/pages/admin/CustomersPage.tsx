import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import type { Customer } from "../../types";

const EMPTY: Partial<Customer> = {
  customer_type: "individual",
  category: "residential",
  full_name: "",
  email: "",
  phone: "",
  address: "",
  city: "",
  status: "new",
};

export function CustomersPage() {
  const navigate = useNavigate();
  const { items, count, loading, refetch } = useApiList<Customer>("/customers/?page_size=100&ordering=-created_at");
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState<Partial<Customer>>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [search, setSearch] = useState("");

  const filtered = items.filter((c) =>
    `${c.full_name} ${c.customer_id} ${c.email}`.toLowerCase().includes(search.toLowerCase())
  );

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await api.post("/customers/", form);
      setShowModal(false);
      setForm(EMPTY);
      refetch();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Customers"
        subtitle={`${count} total customers`}
        actions={
          <button className={btnPrimary} onClick={() => setShowModal(true)}>
            + New customer
          </button>
        }
      />

      <input
        className={`${inputClass} mb-4 max-w-xs`}
        placeholder="Search customers…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Customer</TH>
              <TH>Type</TH>
              <TH>Contact</TH>
              <TH>City</TH>
              <TH>Status</TH>
              <TH>Balance</TH>
              <TH>Assigned to</TH>
            </tr>
          </THead>
          <tbody>
            {filtered.map((c) => (
              <TR key={c.id} onClick={() => navigate(`/admin/customers/${c.id}`)}>
                <TD>
                  <div className="font-medium">{c.full_name}</div>
                  <div className="text-xs text-[var(--text-muted)]">{c.customer_id}</div>
                </TD>
                <TD className="capitalize">{c.category}</TD>
                <TD>
                  <div>{c.email}</div>
                  <div className="text-xs text-[var(--text-muted)]">{c.phone}</div>
                </TD>
                <TD>{c.city}</TD>
                <TD>
                  <StatusBadge status={c.status} />
                </TD>
                <TD className="tabular-nums">R {parseFloat(c.balance).toFixed(2)}</TD>
                <TD>{c.assigned_staff_name ?? "—"}</TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title="New customer" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Full name">
              <input
                className={inputClass}
                required
                value={form.full_name}
                onChange={(e) => setForm({ ...form, full_name: e.target.value })}
              />
            </FormField>
            <FormField label="Email">
              <input
                type="email"
                className={inputClass}
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />
            </FormField>
            <FormField label="Phone">
              <input className={inputClass} value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
            </FormField>
            <FormField label="City">
              <input className={inputClass} value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })} />
            </FormField>
            <FormField label="Category">
              <select
                className={inputClass}
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value as Customer["category"] })}
              >
                <option value="residential">Residential</option>
                <option value="business">Business</option>
              </select>
            </FormField>
            <FormField label="Status">
              <select
                className={inputClass}
                value={form.status}
                onChange={(e) => setForm({ ...form, status: e.target.value as Customer["status"] })}
              >
                <option value="new">New</option>
                <option value="active">Active</option>
                <option value="blocked">Blocked</option>
                <option value="inactive">Inactive</option>
              </select>
            </FormField>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : "Create customer"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
