import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, SortableTH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { CSVImportModal } from "../../components/CSVImportModal";
import type { Customer } from "../../types";

const PAGE_SIZE = 50;

const IMPORT_TEMPLATE_HEADERS = [
  "full_name", "company_name", "email", "phone", "address", "city", "zip_code",
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
};

export function CustomersPage() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [ordering, setOrdering] = useState("full_name");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  // Debounce the search box so we're not hitting the API on every keystroke
  // across 1000+ customers — 300ms after the user stops typing.
  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
    }, 300);
    return () => clearTimeout(t);
  }, [search]);

  const url = `/customers/?page_size=${PAGE_SIZE}&page=${page}&ordering=${ordering}${
    debouncedSearch ? `&search=${encodeURIComponent(debouncedSearch)}` : ""
  }`;
  const { items, count, loading, refetch } = useApiList<Customer>(url);

  const [showModal, setShowModal] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [form, setForm] = useState<Partial<Customer>>(EMPTY);
  const [saving, setSaving] = useState(false);

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

      <input
        className={`${inputClass} mb-4 max-w-xs`}
        placeholder="Search customers…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <>
          <Table>
            <THead>
              <tr>
                <SortableTH field="full_name" ordering={ordering} onSort={toggleSort}>Customer</SortableTH>
                <SortableTH field="category" ordering={ordering} onSort={toggleSort}>Type</SortableTH>
                <TH>Contact</TH>
                <SortableTH field="city" ordering={ordering} onSort={toggleSort}>City</SortableTH>
                <SortableTH field="status" ordering={ordering} onSort={toggleSort}>Status</SortableTH>
                <SortableTH field="balance" ordering={ordering} onSort={toggleSort}>Balance</SortableTH>
                <TH>Assigned to</TH>
              </tr>
            </THead>
            <tbody>
              {items.map((c) => (
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
    </div>
  );
}
