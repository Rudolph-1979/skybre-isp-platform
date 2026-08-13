import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import type { Ticket, Customer } from "../../types";

export function TicketsPage() {
  const navigate = useNavigate();
  const { items, loading, refetch } = useApiList<Ticket>("/tickets/?page_size=100&ordering=-created_at");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ customer: "", subject: "", description: "", department: "support", priority: "medium" });

  useEffect(() => {
    api.get<{ results: Customer[] }>("/customers/?page_size=200").then((res) => setCustomers(res.data.results));
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await api.post("/tickets/", form);
      setShowModal(false);
      refetch();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Support Tickets"
        subtitle="Customer support requests across departments."
        actions={<button className={btnPrimary} onClick={() => setShowModal(true)}>+ New ticket</button>}
      />

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Ticket</TH>
              <TH>Customer</TH>
              <TH>Subject</TH>
              <TH>Department</TH>
              <TH>Priority</TH>
              <TH>Status</TH>
              <TH>Assigned</TH>
            </tr>
          </THead>
          <tbody>
            {items.map((t) => (
              <TR key={t.id} onClick={() => navigate(`/admin/tickets/${t.id}`)}>
                <TD className="font-medium">{t.ticket_number}</TD>
                <TD>{t.customer_name}</TD>
                <TD>{t.subject}</TD>
                <TD className="capitalize">{t.department}</TD>
                <TD><StatusBadge status={t.priority} /></TD>
                <TD><StatusBadge status={t.status} /></TD>
                <TD>{t.assigned_to_name ?? "Unassigned"}</TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title="New ticket" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Customer">
              <select className={inputClass} required value={form.customer} onChange={(e) => setForm({ ...form, customer: e.target.value })}>
                <option value="">Select customer…</option>
                {customers.map((c) => (
                  <option key={c.id} value={c.id}>{c.full_name} ({c.customer_id})</option>
                ))}
              </select>
            </FormField>
            <FormField label="Subject">
              <input className={inputClass} required value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} />
            </FormField>
            <FormField label="Description">
              <textarea className={inputClass} rows={3} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </FormField>
            <FormField label="Department">
              <select className={inputClass} value={form.department} onChange={(e) => setForm({ ...form, department: e.target.value })}>
                <option value="support">Technical Support</option>
                <option value="billing">Billing</option>
                <option value="sales">Sales</option>
                <option value="abuse">Abuse/NOC</option>
              </select>
            </FormField>
            <FormField label="Priority">
              <select className={inputClass} value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="urgent">Urgent</option>
              </select>
            </FormField>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>Cancel</button>
              <button type="submit" disabled={saving} className={btnPrimary}>{saving ? "Saving…" : "Create ticket"}</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
