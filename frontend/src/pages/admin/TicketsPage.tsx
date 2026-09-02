import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { SearchableSelect } from "../../components/SearchableSelect";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, filterSelectClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { ColumnToggle, type ColumnDef } from "../../components/ColumnToggle";
import { useColumnVisibility } from "../../hooks/useColumnVisibility";
import type { Ticket, Customer } from "../../types";

const COLUMNS: ColumnDef[] = [
  { key: "ticket", label: "Ticket" },
  { key: "customer", label: "Customer" },
  { key: "subject", label: "Subject" },
  { key: "department", label: "Department" },
  { key: "priority", label: "Priority" },
  { key: "status", label: "Status" },
  { key: "assigned", label: "Assigned" },
];

export function TicketsPage() {
  const navigate = useNavigate();
  // Seeded from the URL so a dashboard tile can land you on a filtered view
  // rather than the unfiltered page, leaving you to re-apply the filter you
  // just clicked. Lazy initialiser: read once on mount, then it's ordinary
  // state the user is free to change.
  const [searchParams] = useSearchParams();
  const [statusFilter, setStatusFilter] = useState(() => searchParams.get("status") ?? "");
  const [priorityFilter, setPriorityFilter] = useState("");
  const [departmentFilter, setDepartmentFilter] = useState("");
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("tickets", ["ticket"]);
  const { items, loading, refetch } = useApiList<Ticket>(
    `/tickets/?page_size=100&ordering=-created_at${statusFilter ? `&status=${statusFilter}` : ""}${
      priorityFilter ? `&priority=${priorityFilter}` : ""
    }${departmentFilter ? `&department=${departmentFilter}` : ""}`
  );
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ customer: "", subject: "", description: "", department: "support", priority: "medium" });

  useEffect(() => {
    api.get<{ results: Customer[] }>("/customers/?page_size=1000&ordering=full_name").then((res) => setCustomers(res.data.results));
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

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select className={filterSelectClass} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          <option value="open">Open</option>
          <option value="pending">Pending</option>
          <option value="resolved">Resolved</option>
          <option value="closed">Closed</option>
        </select>
        <select className={filterSelectClass} value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value)}>
          <option value="">All priorities</option>
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
          <option value="urgent">Urgent</option>
        </select>
        <select className={filterSelectClass} value={departmentFilter} onChange={(e) => setDepartmentFilter(e.target.value)}>
          <option value="">All departments</option>
          <option value="support">Technical Support</option>
          <option value="billing">Billing</option>
          <option value="sales">Sales</option>
          <option value="abuse">Abuse/NOC</option>
        </select>
        {(statusFilter || priorityFilter || departmentFilter) && (
          <button
            type="button"
            className={btnSecondary}
            onClick={() => {
              setStatusFilter("");
              setPriorityFilter("");
              setDepartmentFilter("");
            }}
          >
            Clear filters
          </button>
        )}
        <div className="ml-auto">
          <ColumnToggle columns={COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["ticket"]} />
        </div>
      </div>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Ticket</TH>
              {isVisible("customer") && <TH>Customer</TH>}
              {isVisible("subject") && <TH>Subject</TH>}
              {isVisible("department") && <TH>Department</TH>}
              {isVisible("priority") && <TH>Priority</TH>}
              {isVisible("status") && <TH>Status</TH>}
              {isVisible("assigned") && <TH>Assigned</TH>}
            </tr>
          </THead>
          <tbody>
            {items.map((t) => (
              <TR key={t.id} onClick={() => navigate(`/admin/tickets/${t.id}`)}>
                <TD className="font-medium">{t.ticket_number}</TD>
                {isVisible("customer") && <TD>{t.customer_name}</TD>}
                {isVisible("subject") && <TD>{t.subject}</TD>}
                {isVisible("department") && <TD className="capitalize">{t.department}</TD>}
                {isVisible("priority") && <TD><StatusBadge status={t.priority} /></TD>}
                {isVisible("status") && <TD><StatusBadge status={t.status} /></TD>}
                {isVisible("assigned") && <TD>{t.assigned_to_name ?? "Unassigned"}</TD>}
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title="New ticket" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Customer">
<SearchableSelect
                options={customers.map((c) => ({
                  value: String(c.id),
                  label: c.full_name,
                  meta: c.customer_id,
                  searchText: `${c.full_name} ${c.company_name ?? ""} ${c.customer_id}`,
                }))}
                value={form.customer}
                onChange={(v) => setForm({ ...form, customer: v })}
                placeholder="Select customer…"
                hint="Search by name or payment reference."
                required
              />
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
