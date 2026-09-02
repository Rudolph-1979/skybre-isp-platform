import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate, Link, useSearchParams } from "react-router-dom";
import { api } from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, SortableTH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, filterSelectClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { CSVImportModal } from "../../components/CSVImportModal";
import { ColumnToggle, type ColumnDef } from "../../components/ColumnToggle";
import { useColumnVisibility } from "../../hooks/useColumnVisibility";
import type { Customer, CustomerDeletionRequest, Partner } from "../../types";

const PAGE_SIZE = 50;

const COLUMNS: ColumnDef[] = [
  { key: "customer", label: "Customer" },
  { key: "type", label: "Type" },
  { key: "contact", label: "Contact" },
  { key: "email", label: "Email" },
  { key: "city", label: "City" },
  { key: "public_ip", label: "Public IP" },
  { key: "status", label: "Status" },
  { key: "balance", label: "Balance" },
  { key: "assigned", label: "Assigned to" },
  { key: "partner", label: "Partner" },
];

// Which of their accessible reseller partners a staff member wants shown
// on this page by default -- a personal preference, persisted server-side
// via PATCH /me/ (User.visible_partners). The set of partners they're
// even allowed to pick from is a separate, Management/Admin-set
// restriction (User.allowed_partners) -- see accounts.views.MeView.patch
// and customers.views.CustomerViewSet.get_queryset on the backend.
function PartnerFilterDropdown({
  allPartners,
  selected,
  onChange,
}: {
  allPartners: Partner[];
  selected: number[];
  onChange: (ids: number[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  if (allPartners.length === 0) return null;

  function toggle(id: number) {
    onChange(selected.includes(id) ? selected.filter((v) => v !== id) : [...selected, id]);
  }

  const label =
    selected.length === 0
      ? "All partners"
      : selected.length === 1
      ? allPartners.find((p) => p.id === selected[0])?.name ?? "1 partner"
      : `${selected.length} partners`;

  return (
    <div className="relative" ref={ref}>
      <button type="button" className={filterSelectClass} onClick={() => setOpen((o) => !o)}>
        {label}
      </button>
      {open && (
        <div className="absolute left-0 z-20 mt-1 w-64 rounded-md border border-[var(--border-hairline)] bg-[var(--surface-1)] p-2 shadow-lg">
          <label className="flex items-center gap-2 rounded px-2 py-1.5 text-sm cursor-pointer hover:bg-[var(--tint-hover)]">
            <input type="checkbox" checked={selected.length === 0} onChange={() => onChange([])} />
            <span className="font-medium">All partners</span>
          </label>
          <div className="my-1 border-t border-[var(--border-hairline)]" />
          {allPartners.map((p) => (
            <label key={p.id} className="flex items-center gap-2 rounded px-2 py-1.5 text-sm cursor-pointer hover:bg-[var(--tint-hover)]">
              <input type="checkbox" checked={selected.includes(p.id)} onChange={() => toggle(p.id)} />
              <span>{p.name}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

const IMPORT_TEMPLATE_HEADERS = [
  "full_name", "company_name", "email", "phone", "address", "city", "zip_code",
  "id_number", "vat_number",
  "customer_type", "category", "status", "balance", "assigned_staff_username", "notes",
];

const EMPTY: Partial<Customer> = {
  customer_type: "individual",
  category: "residential",
  full_name: "",
  email: "",
  phone: "",
  address: "",
  city: "",
  status: "new",
  partner: null,
  // Blank means "generate the next CUS-######". Only filled in for a
  // customer migrated from another system who already has a reference.
  customer_id: "",
};

// Management-only oversight of pending customer-deletion requests --
// deleting a customer cascades away all of their services/invoices/
// payments/tickets/email history, so it needs a Management (or Admin)
// sign-off before it actually happens. See customers.CustomerDeletionRequest
// on the backend and the per-customer request UI on CustomerDetailPage.
function PendingDeletionRequestsPanel() {
  const { items, loading, refetch } = useApiList<CustomerDeletionRequest>(
    "/customer-deletion-requests/?status=pending&page_size=50"
  );
  const [busyId, setBusyId] = useState<number | null>(null);
  const [rejecting, setRejecting] = useState<CustomerDeletionRequest | null>(null);
  const [decisionNote, setDecisionNote] = useState("");

  if (loading || items.length === 0) return null;

  async function handleApprove(reqItem: CustomerDeletionRequest) {
    if (
      !confirm(
        `Permanently delete ${reqItem.customer_name}? This deletes ALL of their services, invoices, payments, ` +
          "credit requests, tickets, and email history, and can't be undone."
      )
    )
      return;
    setBusyId(reqItem.id);
    try {
      await api.post(`/customer-deletion-requests/${reqItem.id}/approve/`);
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      alert(detail || "Couldn't approve this deletion request.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleReject(e: FormEvent) {
    e.preventDefault();
    if (!rejecting) return;
    setBusyId(rejecting.id);
    try {
      await api.post(`/customer-deletion-requests/${rejecting.id}/reject/`, { decision_note: decisionNote });
      setRejecting(null);
      setDecisionNote("");
      refetch();
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="mb-4 rounded-lg border border-red-300 bg-red-50 p-4 dark:border-red-900 dark:bg-red-950">
      <p className="mb-3 text-sm font-semibold text-red-800 dark:text-red-200">
        {items.length} pending customer deletion request{items.length === 1 ? "" : "s"}
      </p>
      <ul className="space-y-2">
        {items.map((r) => (
          <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <div>
              <Link to={`/admin/customers/${r.customer}`} className="font-medium text-[var(--series-1)] hover:underline">
                {r.customer_name}
              </Link>
              <span className="ml-2 text-[var(--text-muted)]">
                {r.reason} — requested by {r.requested_by_name ?? "a staff member"}
              </span>
            </div>
            <div className="flex gap-3">
              <button
                className="text-xs font-medium text-[var(--series-1)] hover:underline"
                disabled={busyId === r.id}
                onClick={() => handleApprove(r)}
              >
                Approve
              </button>
              <button
                className="text-xs font-medium text-[var(--status-critical)] hover:underline"
                disabled={busyId === r.id}
                onClick={() => {
                  setRejecting(r);
                  setDecisionNote("");
                }}
              >
                Reject
              </button>
            </div>
          </li>
        ))}
      </ul>

      {rejecting && (
        <Modal title="Reject deletion request" onClose={() => setRejecting(null)}>
          <form onSubmit={handleReject}>
            <p className="mb-3 text-sm text-[var(--text-secondary)]">
              Rejecting the deletion request for {rejecting.customer_name}. They'll remain on the platform.
            </p>
            <FormField label="Note (optional)">
              <input className={inputClass} value={decisionNote} onChange={(e) => setDecisionNote(e.target.value)} />
            </FormField>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setRejecting(null)}>Cancel</button>
              <button type="submit" className={btnPrimary} disabled={busyId === rejecting.id}>
                {busyId === rejecting.id ? "Rejecting…" : "Reject request"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

export function CustomersPage() {
  const navigate = useNavigate();
  const { user, refreshUser } = useAuth();
  const canDecideDeletion = user?.role === "admin" || user?.role === "management";
  const [page, setPage] = useState(1);
  const [ordering, setOrdering] = useState("full_name");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  // Seeded from the URL so a dashboard tile can land you on a filtered view
  // rather than the unfiltered page, leaving you to re-apply the filter you
  // just clicked. Lazy initialiser: read once on mount, then it's ordinary
  // state the user is free to change.
  const [searchParams] = useSearchParams();
  const [statusFilter, setStatusFilter] = useState(() => searchParams.get("status") ?? "");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  // Set by the dashboard's High alert tile (/admin/customers?high_alert=1).
  // Not a dropdown like the others -- there's nothing to choose, it's either
  // on or off -- so it shows as a removable chip beside the filters instead.
  const [highAlert, setHighAlert] = useState(() => searchParams.get("high_alert") === "1");
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("customers", ["customer"]);

  // Reseller partners this staff member is allowed to see at all (empty
  // allowed_partners on their account = unrestricted, i.e. every partner).
  const { items: allPartners } = useApiList<Partner>("/partners/?page_size=200&ordering=name");
  const accessiblePartners =
    user && user.allowed_partners.length > 0
      ? allPartners.filter((p) => user.allowed_partners.includes(p.id))
      : allPartners;

  // Which of those accessible partners they currently want shown --
  // defaults to their saved preference (User.visible_partners), persisted
  // back to the server whenever they change it.
  const [partnerFilter, setPartnerFilter] = useState<number[]>([]);
  useEffect(() => {
    setPartnerFilter(user?.visible_partners ?? []);
  }, [user]);

  function handlePartnerFilterChange(ids: number[]) {
    setPartnerFilter(ids);
    setPage(1);
    api
      .patch("/me/", { visible_partners: ids })
      .then(() => refreshUser())
      .catch(() => {
        // Non-fatal -- the filter still applies for this session even if
        // saving the preference failed (e.g. a transient network hiccup).
      });
  }

  // Debounce the search box so we're not hitting the API on every keystroke
  // across 1000+ customers — 300ms after the user stops typing.
  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
    }, 300);
    return () => clearTimeout(t);
  }, [search]);

  function resetToFirstPage<T>(setter: (v: T) => void) {
    return (v: T) => {
      setter(v);
      setPage(1);
    };
  }

  // Shared by the paginated list below and the "select all matching these
  // filters" bulk-selection action -- same search/filter params, just
  // without page/page_size/ordering, so /customers/ids/ resolves the
  // exact same set of customers the visible page is drawn from.
  const filterParams = `${debouncedSearch ? `&search=${encodeURIComponent(debouncedSearch)}` : ""}${
    statusFilter ? `&status=${statusFilter}` : ""
  }${categoryFilter ? `&category=${categoryFilter}` : ""}${typeFilter ? `&customer_type=${typeFilter}` : ""}${
    partnerFilter.length > 0 ? `&partner_in=${partnerFilter.join(",")}` : ""
  }${highAlert ? "&high_alert=true" : ""}`;
  const url = `/customers/?page_size=${PAGE_SIZE}&page=${page}&ordering=${ordering}${filterParams}`;
  const { items, count, loading, refetch } = useApiList<Customer>(url);

  const [showModal, setShowModal] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [form, setForm] = useState<Partial<Customer>>(EMPTY);
  const [saving, setSaving] = useState(false);

  // Bulk selection -- per-row checkboxes plus "select all" (current page,
  // or every customer matching the current filters across every page).
  // See customers.CustomerDeletionRequestViewSet.bulk_delete on the
  // backend for how the actual delete/request-for-approval decision is
  // made once something's selected here.
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [selectingAll, setSelectingAll] = useState(false);
  const [showBulkDeleteModal, setShowBulkDeleteModal] = useState(false);
  const [bulkReason, setBulkReason] = useState("");
  const [bulkConfirmText, setBulkConfirmText] = useState("");
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [bulkError, setBulkError] = useState("");
  const [bulkResult, setBulkResult] = useState<{
    deleted: number[];
    requested: number[];
    skipped: { id: number; error: string }[];
  } | null>(null);
  const headerCheckboxRef = useRef<HTMLInputElement>(null);

  const pageIds = items.map((c) => c.id);
  const allOnPageSelected = pageIds.length > 0 && pageIds.every((id) => selectedIds.has(id));
  const someOnPageSelected = pageIds.some((id) => selectedIds.has(id));

  useEffect(() => {
    if (headerCheckboxRef.current) {
      headerCheckboxRef.current.indeterminate = someOnPageSelected && !allOnPageSelected;
    }
  }, [someOnPageSelected, allOnPageSelected]);

  function toggleRow(id: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAllOnPage() {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allOnPageSelected) pageIds.forEach((id) => next.delete(id));
      else pageIds.forEach((id) => next.add(id));
      return next;
    });
  }

  async function selectAllMatchingFilters() {
    setSelectingAll(true);
    try {
      const res = await api.get<{ ids: number[]; count: number }>(`/customers/ids/?${filterParams.replace(/^&/, "")}`);
      setSelectedIds(new Set(res.data.ids));
    } finally {
      setSelectingAll(false);
    }
  }

  function clearSelection() {
    setSelectedIds(new Set());
  }

  function openBulkDelete() {
    setBulkReason("");
    setBulkConfirmText("");
    setBulkError("");
    setBulkResult(null);
    setShowBulkDeleteModal(true);
  }

  async function handleBulkDelete(e: FormEvent) {
    e.preventDefault();
    setBulkDeleting(true);
    setBulkError("");
    try {
      const res = await api.post<{ deleted: number[]; requested: number[]; skipped: { id: number; error: string }[] }>(
        "/customer-deletion-requests/bulk-delete/",
        { customer_ids: Array.from(selectedIds), reason: bulkReason }
      );
      setBulkResult(res.data);
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setBulkError(detail || "Couldn't process the bulk deletion.");
    } finally {
      setBulkDeleting(false);
    }
  }

  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE));

  function toggleSort(field: string) {
    setPage(1);
    setOrdering((prev) => (prev === field ? `-${field}` : prev === `-${field}` ? "full_name" : field));
  }

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
          <>
            <button className={btnSecondary} onClick={() => setShowImport(true)}>
              Import CSV
            </button>
            <button className={btnPrimary} onClick={() => setShowModal(true)}>
              + New customer
            </button>
          </>
        }
      />

      {canDecideDeletion && <PendingDeletionRequestsPanel />}

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {/* The placeholder names the two searches nobody expected to work,
            because a search box that says "Search customers…" reads as
            "search customer NAMES" and there is nothing on screen to
            suggest otherwise. */}
        <input
          className={`${inputClass} max-w-xs`}
          placeholder="Search name, IP, reference…"
          title={
            "Searches every column: name, company, reference, email, phone, address, city, " +
            "status, type, balance, partner, assigned staff — and each customer's public IP " +
            "and PPPoE username."
          }
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select
          className={filterSelectClass}
          value={statusFilter}
          onChange={(e) => resetToFirstPage(setStatusFilter)(e.target.value)}
        >
          <option value="">All statuses</option>
          <option value="new">New</option>
          <option value="active">Active</option>
          <option value="suspended">Suspended</option>
          <option value="bad_debt">Bad Debt</option>
          <option value="inactive">Inactive</option>
        </select>
        <select
          className={filterSelectClass}
          value={categoryFilter}
          onChange={(e) => resetToFirstPage(setCategoryFilter)(e.target.value)}
        >
          <option value="">All categories</option>
          <option value="residential">Residential</option>
          <option value="business">Business</option>
        </select>
        <select
          className={filterSelectClass}
          value={typeFilter}
          onChange={(e) => resetToFirstPage(setTypeFilter)(e.target.value)}
        >
          <option value="">All types</option>
          <option value="individual">Individual</option>
          <option value="company">Company</option>
        </select>
        <PartnerFilterDropdown allPartners={accessiblePartners} selected={partnerFilter} onChange={handlePartnerFilterChange} />
        {/* Says what it is and how to get rid of it. A filter arrived at by
            clicking a tile on another page is one you didn't set here, so
            without this the list just looks short for no stated reason. */}
        {highAlert && (
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-full border border-[var(--status-critical)] bg-[var(--surface-2)] px-3 py-1.5 text-xs text-[var(--text-primary)]"
            title="3+ tickets in a single month, in the last 6 months. Click to remove this filter."
            onClick={() => resetToFirstPage(setHighAlert)(false)}
          >
            High alert only
            <span aria-hidden="true" className="text-[var(--text-muted)]">✕</span>
          </button>
        )}
        {(statusFilter || categoryFilter || typeFilter || search || highAlert || partnerFilter.length > 0) && (
          <button
            type="button"
            className={btnSecondary}
            onClick={() => {
              setStatusFilter("");
              setCategoryFilter("");
              setTypeFilter("");
              setSearch("");
              setHighAlert(false);
              handlePartnerFilterChange([]);
            }}
          >
            Clear filters
          </button>
        )}
        <div className="ml-auto">
          <ColumnToggle columns={COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["customer"]} />
        </div>
      </div>

      {selectedIds.size > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-3 rounded-md border border-[var(--border-hairline)] bg-[var(--tint-hover)] px-3 py-2 text-sm">
          <span className="font-medium">{selectedIds.size} selected</span>
          {selectedIds.size < count && (
            <button
              type="button"
              className="text-[var(--series-1)] hover:underline"
              onClick={selectAllMatchingFilters}
              disabled={selectingAll}
            >
              {selectingAll ? "Selecting…" : `Select all ${count} matching these filters`}
            </button>
          )}
          <button type="button" className="text-[var(--series-1)] hover:underline" onClick={clearSelection}>
            Clear selection
          </button>
          <button
            type="button"
            className="ml-auto rounded-md bg-[var(--status-critical)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
            onClick={openBulkDelete}
          >
            {canDecideDeletion ? `Delete selected (${selectedIds.size})` : `Request deletion for selected (${selectedIds.size})`}
          </button>
        </div>
      )}

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <>
          <Table>
            <THead>
              <tr>
                <TH>
                  <input
                    ref={headerCheckboxRef}
                    type="checkbox"
                    checked={allOnPageSelected}
                    onChange={toggleAllOnPage}
                    aria-label="Select all customers on this page"
                  />
                </TH>
                <SortableTH field="full_name" ordering={ordering} onSort={toggleSort}>Customer</SortableTH>
                {isVisible("type") && <SortableTH field="category" ordering={ordering} onSort={toggleSort}>Type</SortableTH>}
                {isVisible("contact") && <TH>Contact</TH>}
                {isVisible("email") && <TH>Email</TH>}
                {isVisible("city") && <SortableTH field="city" ordering={ordering} onSort={toggleSort}>City</SortableTH>}
                {/* Not sortable: the address lives on the customer's services,
                    so there is no single column on Customer to order by. */}
                {isVisible("public_ip") && <TH>Public IP</TH>}
                {isVisible("status") && <SortableTH field="status" ordering={ordering} onSort={toggleSort}>Status</SortableTH>}
                {isVisible("balance") && <SortableTH field="balance" ordering={ordering} onSort={toggleSort}>Balance</SortableTH>}
                {isVisible("assigned") && <TH>Assigned to</TH>}
                {isVisible("partner") && <TH>Partner</TH>}
              </tr>
            </THead>
            <tbody>
              {items.map((c) => (
                <TR key={c.id} onClick={() => navigate(`/admin/customers/${c.id}`)}>
                  <TD onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selectedIds.has(c.id)}
                      onChange={() => toggleRow(c.id)}
                      aria-label={`Select ${c.full_name}`}
                    />
                  </TD>
                  <TD>
                    <div className="font-medium">{c.full_name}</div>
                    <div className="text-xs text-[var(--text-muted)]">{c.customer_id}</div>
                  </TD>
                  {isVisible("type") && <TD className="capitalize">{c.category}</TD>}
                  {isVisible("contact") && (
                    <TD>
                      <div>{c.email}</div>
                      <div className="text-xs text-[var(--text-muted)]">{c.phone}</div>
                    </TD>
                  )}
                  {isVisible("email") && <TD>{c.email || "—"}</TD>}
                  {isVisible("city") && (
                    <TD>{c.city}</TD>
                  )}
                  {isVisible("public_ip") && (
                    <TD className="whitespace-nowrap font-mono text-xs">
                      {c.public_ips?.length ? (
                        <>
                          {c.public_ips[0]}
                          {/* A second line's address matters when support is
                              tracing one, so say it exists rather than
                              silently showing only the first. */}
                          {c.public_ips.length > 1 && (
                            <span
                              className="ml-1 text-[var(--text-muted)]"
                              title={c.public_ips.join("\n")}
                            >
                              +{c.public_ips.length - 1}
                            </span>
                          )}
                        </>
                      ) : (
                        <span className="text-[var(--text-muted)]">—</span>
                      )}
                    </TD>
                  )}
                  {isVisible("status") && (
                    <TD>
                      <StatusBadge status={c.status} />
                    </TD>
                  )}
                  {isVisible("balance") && <TD className="tabular-nums">R {parseFloat(c.balance).toFixed(2)}</TD>}
                  {isVisible("assigned") && <TD>{c.assigned_staff_name ?? "—"}</TD>}
                  {isVisible("partner") && <TD>{c.partner_name ?? "Direct"}</TD>}
                </TR>
              ))}
            </tbody>
          </Table>

          <div className="mt-3 flex items-center justify-between text-sm text-[var(--text-muted)]">
            <span>
              Showing {items.length === 0 ? 0 : (page - 1) * PAGE_SIZE + 1}–{(page - 1) * PAGE_SIZE + items.length} of {count}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                className={btnSecondary}
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Previous
              </button>
              <span className="px-2 py-2">Page {page} of {totalPages}</span>
              <button
                type="button"
                className={btnSecondary}
                disabled={page >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              >
                Next
              </button>
            </div>
          </div>
        </>
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
            {/* Optional on create -- left blank, the backend generates the
                next CUS-######. Filled in, it's for a customer migrated from
                another system who already has a reference they put on their
                EFTs; bank-feed matching looks for exactly that string. */}
            <FormField label="Payment reference (optional)">
              <input
                className={inputClass}
                value={form.customer_id ?? ""}
                placeholder="Leave blank to generate automatically"
                onChange={(e) => setForm({ ...form, customer_id: e.target.value })}
              />
            </FormField>
            <p className="-mt-2 mb-3 text-xs text-[var(--text-muted)]">
              Only set this if the customer already uses a reference on their payments — an existing
              account number from a previous system, for example. It's what matches their EFTs in Bank
              Feeds.
            </p>
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
                <option value="suspended">Suspended</option>
                <option value="inactive">Inactive</option>
              </select>
            </FormField>
            {accessiblePartners.length > 0 && (
              <FormField label="Partner (reselling)">
                <select
                  className={inputClass}
                  value={form.partner ?? ""}
                  onChange={(e) => setForm({ ...form, partner: e.target.value ? Number(e.target.value) : null })}
                >
                  <option value="">Direct (no partner)</option>
                  {accessiblePartners.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </FormField>
            )}
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

      {showImport && (
        <CSVImportModal
          title="Import customers"
          importUrlBase="/customers/"
          templateHeaders={IMPORT_TEMPLATE_HEADERS}
          templateFilename="customers_template.csv"
          onClose={() => setShowImport(false)}
          onImported={refetch}
        />
      )}

      {showBulkDeleteModal && (
        <Modal
          title={canDecideDeletion ? "Delete selected customers" : "Request deletion for selected customers"}
          onClose={() => setShowBulkDeleteModal(false)}
        >
          {!bulkResult ? (
            <form onSubmit={handleBulkDelete}>
              <p className="mb-3 text-sm text-[var(--text-secondary)]">
                {canDecideDeletion ? (
                  <>
                    This will <strong>permanently delete {selectedIds.size} customer{selectedIds.size === 1 ? "" : "s"}</strong> and
                    everything tied to them — services (and their RADIUS logins/assigned IPs), invoices, payments, credit requests,
                    tickets, and email history. This can't be undone.
                  </>
                ) : (
                  <>
                    This submits a deletion request for {selectedIds.size} customer{selectedIds.size === 1 ? "" : "s"} — nothing is
                    deleted until Management reviews and approves each one.
                  </>
                )}
              </p>
              <FormField label="Reason">
                <input
                  className={inputClass}
                  required
                  value={bulkReason}
                  onChange={(e) => setBulkReason(e.target.value)}
                  placeholder="e.g. Platform reset before go-live"
                />
              </FormField>
              {canDecideDeletion && (
                <FormField
                  label={`Type DELETE to confirm permanently deleting ${selectedIds.size} customer${selectedIds.size === 1 ? "" : "s"}`}
                >
                  <input
                    className={inputClass}
                    value={bulkConfirmText}
                    onChange={(e) => setBulkConfirmText(e.target.value)}
                    placeholder="DELETE"
                  />
                </FormField>
              )}
              {bulkError && <p className="mb-3 text-sm text-[var(--status-critical)]">{bulkError}</p>}
              <div className="mt-4 flex justify-end gap-2">
                <button type="button" className={btnSecondary} onClick={() => setShowBulkDeleteModal(false)}>
                  Cancel
                </button>
                <button
                  type="submit"
                  className={btnPrimary}
                  disabled={bulkDeleting || (canDecideDeletion && bulkConfirmText !== "DELETE")}
                >
                  {bulkDeleting ? "Working…" : canDecideDeletion ? "Permanently delete" : "Submit deletion request"}
                </button>
              </div>
            </form>
          ) : (
            <div>
              <p className="mb-2 text-sm text-[var(--text-secondary)]">
                {bulkResult.deleted.length > 0 && (
                  <>
                    Deleted {bulkResult.deleted.length} customer{bulkResult.deleted.length === 1 ? "" : "s"}.
                    <br />
                  </>
                )}
                {bulkResult.requested.length > 0 && (
                  <>
                    Submitted {bulkResult.requested.length} deletion request{bulkResult.requested.length === 1 ? "" : "s"} for Management
                    to review.
                    <br />
                  </>
                )}
                {bulkResult.skipped.length > 0 && <>{bulkResult.skipped.length} skipped.</>}
              </p>
              {bulkResult.skipped.length > 0 && (
                <ul className="mb-3 max-h-40 list-disc space-y-0.5 overflow-y-auto pl-5 text-xs text-[var(--text-muted)]">
                  {bulkResult.skipped.map((s) => (
                    <li key={s.id}>
                      Customer #{s.id}: {s.error}
                    </li>
                  ))}
                </ul>
              )}
              <div className="mt-4 flex justify-end">
                <button
                  type="button"
                  className={btnPrimary}
                  onClick={() => {
                    setShowBulkDeleteModal(false);
                    clearSelection();
                    refetch();
                  }}
                >
                  Done
                </button>
              </div>
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}
