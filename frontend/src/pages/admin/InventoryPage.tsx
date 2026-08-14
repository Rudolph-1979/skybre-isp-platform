import { useEffect, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, SortableTH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { ColumnToggle, type ColumnDef } from "../../components/ColumnToggle";
import { useColumnVisibility } from "../../hooks/useColumnVisibility";
import type { Supplier, Product, SerializedUnit, StockReceipt, StockIssue, Job, User } from "../../types";

type Tab = "products" | "suppliers" | "receipts" | "issues";

const CATEGORY_LABEL: Record<Product["category"], string> = {
  router: "Router / CPE",
  ont: "ONT",
  cable: "Cable",
  connector: "Connector",
  tool: "Tool / Equipment",
  other: "Other",
};

export function InventoryPage() {
  const [tab, setTab] = useState<Tab>("products");
  const TABS: { key: Tab; label: string }[] = [
    { key: "products", label: "Products" },
    { key: "suppliers", label: "Suppliers" },
    { key: "receipts", label: "Stock Receipts" },
    { key: "issues", label: "Stock Issues" },
  ];

  return (
    <div>
      <PageHeader
        title="Stock / Inventory"
        subtitle="Equipment and consumables — checked in against supplier invoices, issued out to jobs and technicians."
      />
      <div className="mb-4 flex gap-1 border-b border-[var(--border-hairline)]">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium ${
              tab === t.key
                ? "border-b-2 border-[var(--series-1)] text-[var(--text-primary)]"
                : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === "products" && <ProductsTab />}
      {tab === "suppliers" && <SuppliersTab />}
      {tab === "receipts" && <ReceiptsTab />}
      {tab === "issues" && <IssuesTab />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Products
// ---------------------------------------------------------------------------

const EMPTY_PRODUCT: Partial<Product> = {
  name: "",
  sku: "",
  category: "other",
  tracking_type: "quantity",
  unit: "each",
  low_stock_threshold: null,
  description: "",
  is_active: true,
};

const PRODUCT_COLUMNS: ColumnDef[] = [
  { key: "name", label: "Name" },
  { key: "sku", label: "SKU" },
  { key: "category", label: "Category" },
  { key: "tracking", label: "Tracking" },
  { key: "on_hand", label: "On hand" },
  { key: "status", label: "Status" },
];

function ProductsTab() {
  const [ordering, setOrdering] = useState("name");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [trackingFilter, setTrackingFilter] = useState("");
  const [activeFilter, setActiveFilter] = useState("");
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("inventory-products", ["name"]);
  const { items, loading, refetch } = useApiList<Product>(
    `/products/?page_size=200&ordering=${ordering}${categoryFilter ? `&category=${categoryFilter}` : ""}${
      trackingFilter ? `&tracking_type=${trackingFilter}` : ""
    }${activeFilter ? `&is_active=${activeFilter}` : ""}`
  );
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState<Partial<Product>>(EMPTY_PRODUCT);
  const [saving, setSaving] = useState(false);
  const [adjusting, setAdjusting] = useState<Product | null>(null);
  const [adjustQty, setAdjustQty] = useState("");
  const [adjustNote, setAdjustNote] = useState("");

  function toggleSort(field: string) {
    setOrdering((prev) => (prev === field ? `-${field}` : prev === `-${field}` ? "name" : field));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await api.post("/products/", form);
      setShowModal(false);
      setForm(EMPTY_PRODUCT);
      refetch();
    } finally {
      setSaving(false);
    }
  }

  async function handleAdjust(e: FormEvent) {
    e.preventDefault();
    if (!adjusting) return;
    setSaving(true);
    try {
      await api.post(`/products/${adjusting.id}/adjust/`, {
        quantity: Number(adjustQty),
        note: adjustNote,
      });
      setAdjusting(null);
      setAdjustQty("");
      setAdjustNote("");
      refetch();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <select className={`${inputClass} w-auto`} value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
            <option value="">All categories</option>
            {Object.entries(CATEGORY_LABEL).map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </select>
          <select className={`${inputClass} w-auto`} value={trackingFilter} onChange={(e) => setTrackingFilter(e.target.value)}>
            <option value="">All tracking types</option>
            <option value="quantity">By quantity</option>
            <option value="serialized">By serial/MAC</option>
          </select>
          <select className={`${inputClass} w-auto`} value={activeFilter} onChange={(e) => setActiveFilter(e.target.value)}>
            <option value="">Active & inactive</option>
            <option value="true">Active only</option>
            <option value="false">Inactive only</option>
          </select>
          {(categoryFilter || trackingFilter || activeFilter) && (
            <button
              type="button"
              className={btnSecondary}
              onClick={() => {
                setCategoryFilter("");
                setTrackingFilter("");
                setActiveFilter("");
              }}
            >
              Clear filters
            </button>
          )}
          <ColumnToggle columns={PRODUCT_COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["name"]} />
        </div>
        <button className={btnPrimary} onClick={() => setShowModal(true)}>
          + New product
        </button>
      </div>
      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <SortableTH field="name" ordering={ordering} onSort={toggleSort}>Name</SortableTH>
              {isVisible("sku") && <TH>SKU</TH>}
              {isVisible("category") && <SortableTH field="category" ordering={ordering} onSort={toggleSort}>Category</SortableTH>}
              {isVisible("tracking") && <SortableTH field="tracking_type" ordering={ordering} onSort={toggleSort}>Tracking</SortableTH>}
              {isVisible("on_hand") && <TH>On hand</TH>}
              {isVisible("status") && <TH>Status</TH>}
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((p) => (
              <TR key={p.id}>
                <TD className="font-medium">{p.name}</TD>
                {isVisible("sku") && <TD>{p.sku || "—"}</TD>}
                {isVisible("category") && <TD>{CATEGORY_LABEL[p.category]}</TD>}
                {isVisible("tracking") && <TD className="capitalize">{p.tracking_type}</TD>}
                {isVisible("on_hand") && (
                  <TD className="tabular-nums">
                    {p.quantity_on_hand} {p.tracking_type === "quantity" ? p.unit : ""}
                    {p.is_low_stock && (
                      <span className="ml-2 inline-block align-middle">
                        <StatusBadge status="overdue" />
                      </span>
                    )}
                  </TD>
                )}
                {isVisible("status") && (
                  <TD>
                    <StatusBadge status={p.is_active ? "active" : "inactive"} />
                  </TD>
                )}
                <TD>
                  {p.tracking_type === "quantity" && (
                    <button
                      className="text-xs text-[var(--series-1)] hover:underline"
                      onClick={() => setAdjusting(p)}
                    >
                      Adjust
                    </button>
                  )}
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title="New product" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Name">
              <input
                className={inputClass}
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </FormField>
            <FormField label="SKU (optional)">
              <input
                className={inputClass}
                value={form.sku}
                onChange={(e) => setForm({ ...form, sku: e.target.value })}
              />
            </FormField>
            <FormField label="Category">
              <select
                className={inputClass}
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value as Product["category"] })}
              >
                {Object.entries(CATEGORY_LABEL).map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
            </FormField>
            <FormField label="Tracking">
              <select
                className={inputClass}
                value={form.tracking_type}
                onChange={(e) => setForm({ ...form, tracking_type: e.target.value as Product["tracking_type"] })}
              >
                <option value="quantity">By quantity</option>
                <option value="serialized">By serial/MAC (individually tracked)</option>
              </select>
            </FormField>
            {form.tracking_type === "quantity" && (
              <>
                <FormField label="Unit">
                  <input
                    className={inputClass}
                    value={form.unit}
                    onChange={(e) => setForm({ ...form, unit: e.target.value })}
                    placeholder="each, box, meter…"
                  />
                </FormField>
                <FormField label="Low stock threshold (optional)">
                  <input
                    type="number"
                    className={inputClass}
                    value={form.low_stock_threshold ?? ""}
                    onChange={(e) =>
                      setForm({ ...form, low_stock_threshold: e.target.value ? Number(e.target.value) : null })
                    }
                  />
                </FormField>
              </>
            )}
            <FormField label="Description (optional)">
              <textarea
                className={inputClass}
                rows={2}
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
            </FormField>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : "Create product"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {adjusting && (
        <Modal title={`Adjust stock — ${adjusting.name}`} onClose={() => setAdjusting(null)}>
          <form onSubmit={handleAdjust}>
            <p className="mb-3 text-sm text-[var(--text-secondary)]">
              Currently {adjusting.quantity_on_hand} {adjusting.unit} on hand.
            </p>
            <FormField label="Adjustment (positive to add, negative to remove)">
              <input
                type="number"
                className={inputClass}
                required
                value={adjustQty}
                onChange={(e) => setAdjustQty(e.target.value)}
              />
            </FormField>
            <FormField label="Reason">
              <input
                className={inputClass}
                value={adjustNote}
                onChange={(e) => setAdjustNote(e.target.value)}
                placeholder="e.g. damaged, recount…"
              />
            </FormField>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setAdjusting(null)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : "Apply"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Suppliers
// ---------------------------------------------------------------------------

const EMPTY_SUPPLIER: Partial<Supplier> = {
  name: "",
  contact_person: "",
  phone: "",
  email: "",
  address: "",
  notes: "",
};

const SUPPLIER_COLUMNS: ColumnDef[] = [
  { key: "name", label: "Name" },
  { key: "contact_person", label: "Contact person" },
  { key: "phone", label: "Phone" },
  { key: "email", label: "Email" },
];

function SuppliersTab() {
  const [ordering, setOrdering] = useState("name");
  const { items, loading, refetch } = useApiList<Supplier>(`/suppliers/?page_size=200&ordering=${ordering}`);
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("inventory-suppliers", ["name"]);
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState<Partial<Supplier>>(EMPTY_SUPPLIER);
  const [saving, setSaving] = useState(false);

  function toggleSort(field: string) {
    setOrdering((prev) => (prev === field ? `-${field}` : prev === `-${field}` ? "name" : field));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await api.post("/suppliers/", form);
      setShowModal(false);
      setForm(EMPTY_SUPPLIER);
      refetch();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <ColumnToggle columns={SUPPLIER_COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["name"]} />
        <button className={btnPrimary} onClick={() => setShowModal(true)}>
          + New supplier
        </button>
      </div>
      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <SortableTH field="name" ordering={ordering} onSort={toggleSort}>Name</SortableTH>
              {isVisible("contact_person") && <TH>Contact person</TH>}
              {isVisible("phone") && <TH>Phone</TH>}
              {isVisible("email") && <TH>Email</TH>}
            </tr>
          </THead>
          <tbody>
            {items.map((s) => (
              <TR key={s.id}>
                <TD className="font-medium">{s.name}</TD>
                {isVisible("contact_person") && <TD>{s.contact_person || "—"}</TD>}
                {isVisible("phone") && <TD>{s.phone || "—"}</TD>}
                {isVisible("email") && <TD>{s.email || "—"}</TD>}
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title="New supplier" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Name">
              <input
                className={inputClass}
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </FormField>
            <FormField label="Contact person">
              <input
                className={inputClass}
                value={form.contact_person}
                onChange={(e) => setForm({ ...form, contact_person: e.target.value })}
              />
            </FormField>
            <FormField label="Phone">
              <input
                className={inputClass}
                value={form.phone}
                onChange={(e) => setForm({ ...form, phone: e.target.value })}
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
            <FormField label="Address">
              <textarea
                className={inputClass}
                rows={2}
                value={form.address}
                onChange={(e) => setForm({ ...form, address: e.target.value })}
              />
            </FormField>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : "Create supplier"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stock Receipts (check-in against a supplier invoice)
// ---------------------------------------------------------------------------

type ReceiptLineForm = { product: string; quantity: string; serial_numbers: string; unit_cost: string };
const EMPTY_RECEIPT_LINE: ReceiptLineForm = { product: "", quantity: "1", serial_numbers: "", unit_cost: "" };

const RECEIPT_COLUMNS: ColumnDef[] = [
  { key: "date", label: "Date" },
  { key: "invoice_number", label: "Invoice #" },
  { key: "supplier", label: "Supplier" },
  { key: "lines", label: "Lines" },
  { key: "received_by", label: "Received by" },
  { key: "attachment", label: "Attachment" },
];

function ReceiptsTab() {
  const [ordering, setOrdering] = useState("-invoice_date");
  const [supplierFilter, setSupplierFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("inventory-receipts", ["invoice_number"]);
  const { items, loading, refetch } = useApiList<StockReceipt>(
    `/stock-receipts/?page_size=100&ordering=${ordering}${supplierFilter ? `&supplier=${supplierFilter}` : ""}${
      dateFrom ? `&date_from=${dateFrom}` : ""
    }${dateTo ? `&date_to=${dateTo}` : ""}`
  );
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [viewing, setViewing] = useState<StockReceipt | null>(null);
  const [supplier, setSupplier] = useState("");
  const [invoiceNumber, setInvoiceNumber] = useState("");
  const [invoiceDate, setInvoiceDate] = useState("");
  const [notes, setNotes] = useState("");
  const [attachment, setAttachment] = useState<File | null>(null);
  const [lines, setLines] = useState<ReceiptLineForm[]>([{ ...EMPTY_RECEIPT_LINE }]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get<{ results: Supplier[] }>("/suppliers/?page_size=200").then((r) => setSuppliers(r.data.results));
    api
      .get<{ results: Product[] }>("/products/?page_size=500&is_active=true")
      .then((r) => setProducts(r.data.results));
  }, []);

  function toggleSort(field: string) {
    setOrdering((prev) => (prev === field ? `-${field}` : prev === `-${field}` ? "-invoice_date" : field));
  }

  function productFor(id: string) {
    return products.find((p) => String(p.id) === id);
  }

  function updateLine(index: number, patch: Partial<ReceiptLineForm>) {
    setLines((prev) => prev.map((l, i) => (i === index ? { ...l, ...patch } : l)));
  }

  function addLine() {
    setLines((prev) => [...prev, { ...EMPTY_RECEIPT_LINE }]);
  }

  function removeLine(index: number) {
    setLines((prev) => prev.filter((_, i) => i !== index));
  }

  function resetForm() {
    setSupplier("");
    setInvoiceNumber("");
    setInvoiceDate("");
    setNotes("");
    setAttachment(null);
    setLines([{ ...EMPTY_RECEIPT_LINE }]);
    setError("");
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      const payload = {
        supplier: Number(supplier),
        invoice_number: invoiceNumber,
        invoice_date: invoiceDate,
        notes,
        lines: lines.map((l) => {
          const product = productFor(l.product);
          const base: Record<string, unknown> = { product: Number(l.product), unit_cost: l.unit_cost || null };
          if (product?.tracking_type === "serialized") {
            base.serial_numbers = l.serial_numbers;
          } else {
            base.quantity = Number(l.quantity) || 0;
          }
          return base;
        }),
      };
      const res = await api.post<StockReceipt>("/stock-receipts/", payload);
      if (attachment) {
        const fd = new FormData();
        fd.append("attachment", attachment);
        await api.patch(`/stock-receipts/${res.data.id}/`, fd, {
          headers: { "Content-Type": "multipart/form-data" },
        });
      }
      setShowModal(false);
      resetForm();
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: unknown } })?.response?.data as
        | { detail?: string }
        | string
        | undefined;
      setError(
        typeof detail === "string" ? detail : detail?.detail || JSON.stringify(detail) || "Failed to save receipt."
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-end gap-2">
          <select className={`${inputClass} w-auto`} value={supplierFilter} onChange={(e) => setSupplierFilter(e.target.value)}>
            <option value="">All suppliers</option>
            {suppliers.map((s) => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
          <FormField label="Invoice date from">
            <input type="date" className={inputClass} value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
          </FormField>
          <FormField label="Invoice date to">
            <input type="date" className={inputClass} value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
          </FormField>
          {(supplierFilter || dateFrom || dateTo) && (
            <button
              type="button"
              className={`${btnSecondary} mb-3`}
              onClick={() => {
                setSupplierFilter("");
                setDateFrom("");
                setDateTo("");
              }}
            >
              Clear filters
            </button>
          )}
          <ColumnToggle columns={RECEIPT_COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["invoice_number"]} />
        </div>
        <button className={btnPrimary} onClick={() => setShowModal(true)}>
          + New receipt
        </button>
      </div>
      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              {isVisible("date") && <SortableTH field="invoice_date" ordering={ordering} onSort={toggleSort}>Date</SortableTH>}
              <TH>Invoice #</TH>
              {isVisible("supplier") && <SortableTH field="supplier__name" ordering={ordering} onSort={toggleSort}>Supplier</SortableTH>}
              {isVisible("lines") && <TH>Lines</TH>}
              {isVisible("received_by") && <TH>Received by</TH>}
              {isVisible("attachment") && <TH>Attachment</TH>}
            </tr>
          </THead>
          <tbody>
            {items.map((r) => (
              <TR key={r.id} onClick={() => setViewing(r)}>
                {isVisible("date") && <TD>{r.invoice_date}</TD>}
                <TD className="font-medium">{r.invoice_number}</TD>
                {isVisible("supplier") && <TD>{r.supplier_name}</TD>}
                {isVisible("lines") && <TD>{r.lines.length}</TD>}
                {isVisible("received_by") && <TD>{r.received_by_name || "—"}</TD>}
                {isVisible("attachment") && <TD>{r.attachment ? "Yes" : "—"}</TD>}
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title="New stock receipt" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            {error && (
              <p className="mb-3 rounded-md bg-[#fbeaea] px-3 py-2 text-sm text-[#b32e2e]">{error}</p>
            )}
            <FormField label="Supplier">
              <select className={inputClass} required value={supplier} onChange={(e) => setSupplier(e.target.value)}>
                <option value="">Select a supplier…</option>
                {suppliers.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </FormField>
            <FormField label="Supplier invoice number">
              <input
                className={inputClass}
                required
                value={invoiceNumber}
                onChange={(e) => setInvoiceNumber(e.target.value)}
              />
            </FormField>
            <FormField label="Invoice date">
              <input
                type="date"
                className={inputClass}
                required
                value={invoiceDate}
                onChange={(e) => setInvoiceDate(e.target.value)}
              />
            </FormField>
            <FormField label="Attachment (optional — photo/PDF of the invoice)">
              <input
                type="file"
                className={inputClass}
                onChange={(e) => setAttachment(e.target.files?.[0] ?? null)}
              />
            </FormField>

            <div className="mb-2 mt-4 text-sm font-medium text-[var(--text-secondary)]">Items received</div>
            {lines.map((line, i) => {
              const product = productFor(line.product);
              return (
                <div key={i} className="mb-3 rounded-md border border-[var(--border-hairline)] p-3">
                  <FormField label="Product">
                    <select
                      className={inputClass}
                      required
                      value={line.product}
                      onChange={(e) => updateLine(i, { product: e.target.value })}
                    >
                      <option value="">Select a product…</option>
                      {products.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                  </FormField>
                  {product?.tracking_type === "serialized" ? (
                    <FormField label="Serial numbers (one per line, optionally 'SERIAL,MAC')">
                      <textarea
                        className={inputClass}
                        rows={3}
                        value={line.serial_numbers}
                        onChange={(e) => updateLine(i, { serial_numbers: e.target.value })}
                        placeholder={"SN12345,AA:BB:CC:DD:EE:FF\nSN12346"}
                      />
                    </FormField>
                  ) : (
                    <FormField label={`Quantity${product ? ` (${product.unit})` : ""}`}>
                      <input
                        type="number"
                        min={1}
                        className={inputClass}
                        value={line.quantity}
                        onChange={(e) => updateLine(i, { quantity: e.target.value })}
                      />
                    </FormField>
                  )}
                  <FormField label="Unit cost (optional, R)">
                    <input
                      type="number"
                      step="0.01"
                      className={inputClass}
                      value={line.unit_cost}
                      onChange={(e) => updateLine(i, { unit_cost: e.target.value })}
                    />
                  </FormField>
                  {lines.length > 1 && (
                    <button
                      type="button"
                      className="text-xs text-[#b32e2e] hover:underline"
                      onClick={() => removeLine(i)}
                    >
                      Remove line
                    </button>
                  )}
                </div>
              );
            })}
            <button type="button" className={`${btnSecondary} mb-3`} onClick={addLine}>
              + Add another item
            </button>

            <FormField label="Notes (optional)">
              <textarea className={inputClass} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
            </FormField>

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : "Check in stock"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {viewing && (
        <Modal title={`Receipt ${viewing.invoice_number}`} onClose={() => setViewing(null)}>
          <p className="mb-1 text-sm">
            <span className="font-medium">Supplier:</span> {viewing.supplier_name}
          </p>
          <p className="mb-1 text-sm">
            <span className="font-medium">Date:</span> {viewing.invoice_date}
          </p>
          {viewing.received_by_name && (
            <p className="mb-1 text-sm">
              <span className="font-medium">Received by:</span> {viewing.received_by_name}
            </p>
          )}
          {viewing.notes && (
            <p className="mb-1 text-sm">
              <span className="font-medium">Notes:</span> {viewing.notes}
            </p>
          )}
          {viewing.attachment && (
            <p className="mb-3 text-sm">
              <a
                href={viewing.attachment}
                target="_blank"
                rel="noreferrer"
                className="text-[var(--series-1)] hover:underline"
              >
                View attached invoice
              </a>
            </p>
          )}
          <div className="mt-3 space-y-2">
            {viewing.lines.map((l) => (
              <div key={l.id} className="rounded-md border border-[var(--border-hairline)] p-2 text-sm">
                <div className="font-medium">{l.product_name}</div>
                {l.serial_numbers ? (
                  <div className="whitespace-pre-line text-xs text-[var(--text-secondary)]">
                    {l.serial_numbers}
                  </div>
                ) : (
                  <div className="text-xs text-[var(--text-secondary)]">
                    Qty: {l.quantity}
                    {l.unit_cost ? ` · R ${parseFloat(l.unit_cost).toFixed(2)} each` : ""}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stock Issues (issued out to jobs / technicians)
// ---------------------------------------------------------------------------

type IssueLineForm = { product: string; quantity: string; serial_unit: string };
const EMPTY_ISSUE_LINE: IssueLineForm = { product: "", quantity: "1", serial_unit: "" };

const ISSUE_COLUMNS: ColumnDef[] = [
  { key: "date", label: "Date" },
  { key: "job", label: "Job / Customer" },
  { key: "issued_to", label: "Issued to" },
  { key: "items", label: "Items" },
];

function IssuesTab() {
  const [ordering, setOrdering] = useState("-issued_at");
  const [issuedToFilter, setIssuedToFilter] = useState("");
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("inventory-issues", ["date"]);
  const { items, loading, refetch } = useApiList<StockIssue>(
    `/stock-issues/?page_size=100&ordering=${ordering}${issuedToFilter ? `&issued_to=${issuedToFilter}` : ""}`
  );
  const [products, setProducts] = useState<Product[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [staff, setStaff] = useState<User[]>([]);
  const [availableUnits, setAvailableUnits] = useState<Record<string, SerializedUnit[]>>({});
  const [showModal, setShowModal] = useState(false);
  const [viewing, setViewing] = useState<StockIssue | null>(null);
  const [job, setJob] = useState("");
  const [issuedTo, setIssuedTo] = useState("");
  const [notes, setNotes] = useState("");
  const [lines, setLines] = useState<IssueLineForm[]>([{ ...EMPTY_ISSUE_LINE }]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .get<{ results: Product[] }>("/products/?page_size=500&is_active=true")
      .then((r) => setProducts(r.data.results));
    api.get<{ results: Job[] }>("/jobs/?page_size=200&ordering=-start").then((r) => setJobs(r.data.results));
    api.get<{ results: User[] }>("/staff-users/?page_size=100").then((r) => setStaff(r.data.results));
  }, []);

  function toggleSort(field: string) {
    setOrdering((prev) => (prev === field ? `-${field}` : prev === `-${field}` ? "-issued_at" : field));
  }

  function productFor(id: string) {
    return products.find((p) => String(p.id) === id);
  }

  async function loadUnitsFor(productId: string) {
    if (availableUnits[productId]) return;
    const res = await api.get<{ results: SerializedUnit[] }>(
      `/serialized-units/?product=${productId}&status=in_stock&page_size=200`
    );
    setAvailableUnits((prev) => ({ ...prev, [productId]: res.data.results }));
  }

  function updateLine(index: number, patch: Partial<IssueLineForm>) {
    setLines((prev) => prev.map((l, i) => (i === index ? { ...l, ...patch } : l)));
  }

  async function handleProductChange(index: number, productId: string) {
    updateLine(index, { product: productId, serial_unit: "" });
    const product = productFor(productId);
    if (product?.tracking_type === "serialized") {
      await loadUnitsFor(productId);
    }
  }

  function addLine() {
    setLines((prev) => [...prev, { ...EMPTY_ISSUE_LINE }]);
  }

  function removeLine(index: number) {
    setLines((prev) => prev.filter((_, i) => i !== index));
  }

  function resetForm() {
    setJob("");
    setIssuedTo("");
    setNotes("");
    setLines([{ ...EMPTY_ISSUE_LINE }]);
    setError("");
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      const payload = {
        job: job ? Number(job) : null,
        issued_to: issuedTo ? Number(issuedTo) : null,
        notes,
        lines: lines.map((l) => {
          const product = productFor(l.product);
          if (product?.tracking_type === "serialized") {
            const unit = availableUnits[l.product]?.find((u) => String(u.id) === l.serial_unit);
            return { product: Number(l.product), serial_number: unit?.serial_number || "" };
          }
          return { product: Number(l.product), quantity: Number(l.quantity) || 0 };
        }),
      };
      await api.post("/stock-issues/", payload);
      setShowModal(false);
      resetForm();
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: unknown } })?.response?.data as
        | { detail?: string }
        | string
        | undefined;
      setError(
        typeof detail === "string" ? detail : detail?.detail || JSON.stringify(detail) || "Failed to save issue."
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <select className={`${inputClass} w-auto`} value={issuedToFilter} onChange={(e) => setIssuedToFilter(e.target.value)}>
            <option value="">All staff</option>
            {staff.map((s) => (
              <option key={s.id} value={s.id}>{s.first_name || s.username}</option>
            ))}
          </select>
          {issuedToFilter && (
            <button type="button" className={btnSecondary} onClick={() => setIssuedToFilter("")}>
              Clear filters
            </button>
          )}
          <ColumnToggle columns={ISSUE_COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["date"]} />
        </div>
        <button className={btnPrimary} onClick={() => setShowModal(true)}>
          + New issue
        </button>
      </div>
      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <SortableTH field="issued_at" ordering={ordering} onSort={toggleSort}>Date</SortableTH>
              {isVisible("job") && <TH>Job / Customer</TH>}
              {isVisible("issued_to") && <SortableTH field="issued_to__username" ordering={ordering} onSort={toggleSort}>Issued to</SortableTH>}
              {isVisible("items") && <TH>Items</TH>}
            </tr>
          </THead>
          <tbody>
            {items.map((iss) => (
              <TR key={iss.id} onClick={() => setViewing(iss)}>
                <TD>{new Date(iss.issued_at).toLocaleString()}</TD>
                {isVisible("job") && (
                  <TD>
                    {iss.job_title
                      ? `${iss.job_title}${iss.customer_name ? ` (${iss.customer_name})` : ""}`
                      : "Standalone"}
                  </TD>
                )}
                {isVisible("issued_to") && <TD>{iss.issued_to_name || "—"}</TD>}
                {isVisible("items") && <TD>{iss.lines.length}</TD>}
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title="New stock issue" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            {error && (
              <p className="mb-3 rounded-md bg-[#fbeaea] px-3 py-2 text-sm text-[#b32e2e]">{error}</p>
            )}
            <FormField label="Job (optional — leave blank for standalone)">
              <select className={inputClass} value={job} onChange={(e) => setJob(e.target.value)}>
                <option value="">No job (standalone)</option>
                {jobs.map((j) => (
                  <option key={j.id} value={j.id}>
                    {j.title}
                    {j.customer_name ? ` — ${j.customer_name}` : ""}
                  </option>
                ))}
              </select>
            </FormField>
            <FormField label="Issued to (technician/staff)">
              <select className={inputClass} value={issuedTo} onChange={(e) => setIssuedTo(e.target.value)}>
                <option value="">Select staff…</option>
                {staff.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.username}
                  </option>
                ))}
              </select>
            </FormField>

            <div className="mb-2 mt-4 text-sm font-medium text-[var(--text-secondary)]">Items issued</div>
            {lines.map((line, i) => {
              const product = productFor(line.product);
              const units = availableUnits[line.product] || [];
              return (
                <div key={i} className="mb-3 rounded-md border border-[var(--border-hairline)] p-3">
                  <FormField label="Product">
                    <select
                      className={inputClass}
                      required
                      value={line.product}
                      onChange={(e) => handleProductChange(i, e.target.value)}
                    >
                      <option value="">Select a product…</option>
                      {products.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name} ({p.quantity_on_hand} {p.tracking_type === "quantity" ? p.unit : "in stock"})
                        </option>
                      ))}
                    </select>
                  </FormField>
                  {product?.tracking_type === "serialized" ? (
                    <FormField label="Serial number">
                      <select
                        className={inputClass}
                        required
                        value={line.serial_unit}
                        onChange={(e) => updateLine(i, { serial_unit: e.target.value })}
                      >
                        <option value="">{units.length ? "Select a unit…" : "Loading…"}</option>
                        {units.map((u) => (
                          <option key={u.id} value={u.id}>
                            {u.serial_number}
                            {u.mac_address ? ` (${u.mac_address})` : ""}
                          </option>
                        ))}
                      </select>
                    </FormField>
                  ) : (
                    <FormField
                      label={`Quantity${
                        product ? ` (${product.unit}, ${product.quantity_on_hand} available)` : ""
                      }`}
                    >
                      <input
                        type="number"
                        min={1}
                        className={inputClass}
                        value={line.quantity}
                        onChange={(e) => updateLine(i, { quantity: e.target.value })}
                      />
                    </FormField>
                  )}
                  {lines.length > 1 && (
                    <button
                      type="button"
                      className="text-xs text-[#b32e2e] hover:underline"
                      onClick={() => removeLine(i)}
                    >
                      Remove line
                    </button>
                  )}
                </div>
              );
            })}
            <button type="button" className={`${btnSecondary} mb-3`} onClick={addLine}>
              + Add another item
            </button>

            <FormField label="Notes (optional)">
              <textarea className={inputClass} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
            </FormField>

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : "Issue stock"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {viewing && (
        <Modal title={`Issue #${viewing.id}`} onClose={() => setViewing(null)}>
          <p className="mb-1 text-sm">
            <span className="font-medium">Job:</span> {viewing.job_title || "Standalone"}
          </p>
          {viewing.customer_name && (
            <p className="mb-1 text-sm">
              <span className="font-medium">Customer:</span> {viewing.customer_name}
            </p>
          )}
          <p className="mb-1 text-sm">
            <span className="font-medium">Issued to:</span> {viewing.issued_to_name || "—"}
          </p>
          <p className="mb-1 text-sm">
            <span className="font-medium">Date:</span> {new Date(viewing.issued_at).toLocaleString()}
          </p>
          {viewing.notes && (
            <p className="mb-3 text-sm">
              <span className="font-medium">Notes:</span> {viewing.notes}
            </p>
          )}
          <div className="mt-3 space-y-2">
            {viewing.lines.map((l) => (
              <div key={l.id} className="rounded-md border border-[var(--border-hairline)] p-2 text-sm">
                <div className="font-medium">{l.product_name}</div>
                <div className="text-xs text-[var(--text-secondary)]">
                  {l.serial_number
                    ? `Serial: ${l.serial_number}${l.mac_address ? ` (${l.mac_address})` : ""}`
                    : `Qty: ${l.quantity}`}
                </div>
              </div>
            ))}
          </div>
        </Modal>
      )}
    </div>
  );
}
