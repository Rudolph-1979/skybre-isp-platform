import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { usePolling } from "../../hooks/usePolling";
import { PageHeader } from "../../components/PageHeader";
import { SearchableSelect } from "../../components/SearchableSelect";
import { StatCard } from "../../components/StatCard";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, filterSelectClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { ColumnToggle, type ColumnDef } from "../../components/ColumnToggle";
import { useColumnVisibility } from "../../hooks/useColumnVisibility";
import { formatIsoDateTime } from "../../utils/billingTerms";
import type { Ticket, Customer, TicketStatusCounts } from "../../types";

const COLUMNS: ColumnDef[] = [
  { key: "ticket", label: "Ticket" },
  { key: "customer", label: "Customer" },
  { key: "subject", label: "Subject" },
  { key: "department", label: "Department" },
  { key: "priority", label: "Priority" },
  { key: "status", label: "Status" },
  { key: "assigned", label: "Assigned" },
  { key: "accepted_by", label: "Accepted by" },
  { key: "created_by", label: "Created by" },
  { key: "created_at", label: "Created" },
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
  // "" means everything. The default is deliberately not "client only":
  // a technician's "no stock" is the most time-critical thing on this
  // page, and hiding it behind a filter nobody knows to change is how it
  // sits unread until they are back at the depot.
  const [kindFilter, setKindFilter] = useState("");
  // Not one of the four dropdowns above -- there's no "escalated" status of
  // its own, a ticket in any status can have a pending escalation sitting
  // on it. Tile-only, like the others it's mutually exclusive with (see
  // selectEscalatedTile): a customer's ticket can be Open AND escalated at
  // once, so mixing this into the status dropdown would either hide it
  // there or force a ticket into two dropdown values simultaneously.
  const [escalatedFilter, setEscalatedFilter] = useState(false);
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("tickets", ["ticket"]);
  const { items, loading, refetch, refetchQuietly } = useApiList<Ticket>(
    `/tickets/?page_size=100&ordering=-created_at${statusFilter ? `&status=${statusFilter}` : ""}${
      priorityFilter ? `&priority=${priorityFilter}` : ""
    }${departmentFilter ? `&department=${departmentFilter}` : ""}${
      kindFilter ? `&is_internal=${kindFilter}` : ""
    }${escalatedFilter ? `&escalated=true` : ""}`
  );
  // One number per status for the tile row above the filters. Deliberately
  // its own request rather than counted from `items`: that array is capped
  // at page_size=100, so counting in the browser would silently under-report
  // once any one status passes 100 tickets -- see TicketViewSet.status_counts,
  // which computes this off the same scoped queryset the list itself uses.
  const [statusCounts, setStatusCounts] = useState<TicketStatusCounts | null>(null);
  function loadStatusCounts() {
    api.get<TicketStatusCounts>("/tickets/status-counts/").then((res) => setStatusCounts(res.data));
  }
  useEffect(() => {
    loadStatusCounts();
  }, []);

  // Tickets now arrive from the Conecto app by webhook, so this list goes
  // stale while somebody is looking straight at it -- and nothing on the
  // page gives them any reason to press refresh. Twenty seconds, quiet,
  // and only while the tab is in front. See usePolling.
  //
  // The status tiles above the table poll on the same tick as the list --
  // one tile reading a stale count next to a table that just updated would
  // be its own small bug report.
  usePolling(() => {
    refetchQuietly();
    loadStatusCounts();
  }, 20_000);

  // A tile sets ONLY the status filter, clearing the other four -- so the
  // number on the tile and the count of the list it lands on can never
  // disagree. Leaving priority/department/kind/escalated applied would make
  // "8 closed" land on a list of 3 the moment any other filter was still
  // set, which is worse than not having the tile at all.
  function selectStatusTile(status: string) {
    setStatusFilter(status);
    setPriorityFilter("");
    setDepartmentFilter("");
    setKindFilter("");
    setEscalatedFilter(false);
  }

  // Same rule as selectStatusTile, the other way round: clicking Escalated
  // clears status/priority/department/kind so the escalated count and the
  // list it lands on always agree, whatever status those escalated tickets
  // happen to be sitting in.
  function selectEscalatedTile() {
    setStatusFilter("");
    setPriorityFilter("");
    setDepartmentFilter("");
    setKindFilter("");
    setEscalatedFilter(true);
  }

  const [customers, setCustomers] = useState<Customer[]>([]);
  // ?new=1 opens the New ticket form straight away -- the Support page's
  // "+ New ticket" button lands here (#212).
  const [showModal, setShowModal] = useState(() => searchParams.get("new") === "1");
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ customer: "", subject: "", description: "", department: "support", priority: "medium" });

  useEffect(() => {
    api.get<{ results: Customer[] }>("/customers/picker/").then((res) => setCustomers(res.data.results));
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await api.post("/tickets/", form);
      setShowModal(false);
      refetch();
      loadStatusCounts();
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

      {/* Each tile sets the status filter below (see selectStatusTile), so
          the number you clicked and the list you land on always agree.
          Clickable even at zero -- "0 pending" is a real answer, not a
          reason to hide the tile (same reasoning as the dashboard's
          blocking/high-alert tiles). */}
      <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard
          label="Open"
          value={statusCounts?.open ?? "—"}
          accent="status-warning"
          onClick={statusCounts ? () => selectStatusTile("open") : undefined}
          actionLabel="Show open tickets"
        />
        <StatCard
          label="Pending Customer"
          value={statusCounts?.pending ?? "—"}
          accent="series-3"
          onClick={statusCounts ? () => selectStatusTile("pending") : undefined}
          actionLabel="Show tickets pending customer"
        />
        <StatCard
          label="Resolved"
          value={statusCounts?.resolved ?? "—"}
          accent="status-good"
          onClick={statusCounts ? () => selectStatusTile("resolved") : undefined}
          actionLabel="Show resolved tickets"
        />
        <StatCard
          label="Closed"
          value={statusCounts?.closed ?? "—"}
          accent="series-1"
          onClick={statusCounts ? () => selectStatusTile("closed") : undefined}
          actionLabel="Show closed tickets"
        />
        {/* Critical-red once there's at least one, same convention the
            dashboard already uses for "devices offline" / "bad debt" --
            a pending escalation is exactly that kind of needs-attention
            count, not a routine one. */}
        <StatCard
          label="Escalated"
          value={statusCounts?.escalated ?? "—"}
          accent={(statusCounts?.escalated ?? 0) > 0 ? "status-critical" : "status-good"}
          onClick={statusCounts ? selectEscalatedTile : undefined}
          actionLabel="Show escalated tickets"
        />
      </div>

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
        <select className={filterSelectClass} value={kindFilter} onChange={(e) => setKindFilter(e.target.value)}>
          <option value="">All tickets</option>
          <option value="false">Customer tickets</option>
          <option value="true">Job feedback</option>
        </select>
        {/* None of the four dropdowns above show this filter (see
            escalatedFilter's declaration) -- without some on-screen sign,
            "why is the list short and every dropdown says All?" is a
            reasonable question. */}
        {escalatedFilter && (
          <span
            className="rounded px-2 py-1 text-xs font-semibold uppercase tracking-wide"
            style={{ background: "#fff6e5", color: "#a5730a" }}
          >
            Escalated only
          </span>
        )}
        {(statusFilter || priorityFilter || departmentFilter || kindFilter || escalatedFilter) && (
          <button
            type="button"
            className={btnSecondary}
            onClick={() => {
              setStatusFilter("");
              setPriorityFilter("");
              setDepartmentFilter("");
              setKindFilter("");
              setEscalatedFilter(false);
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
              {isVisible("accepted_by") && <TH>Accepted by</TH>}
              {isVisible("created_by") && <TH>Created by</TH>}
              {isVisible("created_at") && <TH>Created</TH>}
            </tr>
          </THead>
          <tbody>
            {items.map((t) => (
              <TR key={t.id} onClick={() => navigate(`/admin/tickets/${t.id}`)}>
                <TD className="font-medium">
                  {t.ticket_number}
                  {t.is_internal && (
                    <span
                      className="ml-2 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
                      style={{ background: "var(--surface-2)", color: "var(--text-secondary)" }}
                      title="Raised by a technician from a job. Never visible to a customer."
                    >
                      Internal
                    </span>
                  )}
                  {t.pending_escalation && (
                    <span
                      className="ml-2 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
                      style={{ background: "#fff6e5", color: "#a5730a" }}
                      title="Escalated to NOC, awaiting a decision"
                    >
                      Escalated
                    </span>
                  )}
                </TD>
                {/* Not an empty cell. An internal ticket has no customer by
                    design, and a blank here reads as missing data somebody
                    should go and fix. */}
                {isVisible("customer") && (
                  <TD>
                    {t.customer_name ?? (
                      <span className="italic text-[var(--text-muted)]">
                        {t.is_internal ? "Job feedback" : "—"}
                      </span>
                    )}
                  </TD>
                )}
                {isVisible("subject") && <TD>{t.subject}</TD>}
                {isVisible("department") && <TD className="capitalize">{t.department}</TD>}
                {isVisible("priority") && <TD><StatusBadge status={t.priority} /></TD>}
                {isVisible("status") && <TD><StatusBadge status={t.status} /></TD>}
                {isVisible("assigned") && <TD>{t.assigned_to_name ?? "Unassigned"}</TD>}
                {isVisible("accepted_by") && (
                  <TD>{t.accepted_by_name ?? <span className="text-[var(--text-muted)]">Not yet accepted</span>}</TD>
                )}
                {/* An em dash, not "System" or "Unknown": these are
                    tickets raised before the field existed, and
                    naming a culprit we do not have would be worse
                    than saying nothing. */}
                {isVisible("created_by") && (
                  <TD>{t.created_by_name ?? <span className="text-[var(--text-muted)]">—</span>}</TD>
                )}
                {isVisible("created_at") && (
                  <TD className="whitespace-nowrap">{formatIsoDateTime(t.created_at)}</TD>
                )}
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
