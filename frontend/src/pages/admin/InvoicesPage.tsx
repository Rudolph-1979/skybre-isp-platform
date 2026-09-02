import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, SortableTH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, filterSelectClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { ColumnToggle, type ColumnDef } from "../../components/ColumnToggle";
import { PdfPreviewModal } from "../../components/PdfPreviewModal";
import { useColumnVisibility } from "../../hooks/useColumnVisibility";
import type { Invoice, Customer, Product, Tariff, InvoiceItemType } from "../../types";

// The three document kinds share one table and one PDF endpoint; only the
// wording differs, and it follows the row's own status.
function docNounFor(status: Invoice["status"]) {
  return status === "quote" ? "quote" : status === "proforma" ? "pro forma" : "invoice";
}

const COLUMNS: ColumnDef[] = [
  { key: "number", label: "Number" },
  { key: "customer", label: "Customer" },
  { key: "due_date", label: "Due date" },
  { key: "total", label: "Total" },
  { key: "paid", label: "Paid" },
  { key: "status", label: "Status" },
  { key: "preview", label: "Preview" },
];

interface LineItem {
  itemType: InvoiceItemType;
  product: string;
  tariff: string;
  description: string;
  quantity: string;
  unit_price: string;
  tax_rate_pct: string;
  // Only used when itemType is "tariff" -- the contract/service period
  // being quoted. Required for tariff lines (see billing/serializers.py's
  // InvoiceItemSerializer.validate()) since it's what turns into a real
  // Service subscription once this converts to an invoice.
  period_start: string;
  period_end: string;
}

const BLANK_LINE_ITEM: LineItem = {
  itemType: "custom",
  product: "",
  tariff: "",
  description: "",
  quantity: "1",
  unit_price: "",
  tax_rate_pct: "15",
  period_start: "",
  period_end: "",
};

type DateFilterMode = "all" | "30" | "60" | "90" | "custom";

const DATE_FILTER_TABS: { mode: DateFilterMode; label: string }[] = [
  { mode: "all", label: "All" },
  { mode: "30", label: "0–30 days overdue" },
  { mode: "60", label: "0–60 days overdue" },
  { mode: "90", label: "0–90 days overdue" },
  { mode: "custom", label: "Custom date range" },
];

export function InvoicesPage() {
  const [ordering, setOrdering] = useState("-date_created");
  const [dateFilterMode, setDateFilterMode] = useState<DateFilterMode>("all");
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("invoices", ["number"]);

  let dateParams = "";
  if (dateFilterMode === "30" || dateFilterMode === "60" || dateFilterMode === "90") {
    dateParams = `&overdue_within_days=${dateFilterMode}`;
  } else if (dateFilterMode === "custom") {
    if (customFrom) dateParams += `&date_created_from=${customFrom}`;
    if (customTo) dateParams += `&date_created_to=${customTo}`;
  }

  const { items, count, loading, refetch } = useApiList<Invoice>(
    `/invoices/?page_size=100&ordering=${ordering}${dateParams}${statusFilter ? `&status=${statusFilter}` : ""}`
  );
  const [customers, setCustomers] = useState<Customer[]>([]);

  function toggleSort(field: string) {
    setOrdering((prev) => (prev === field ? `-${field}` : prev === `-${field}` ? "-date_created" : field));
  }
  const [showModal, setShowModal] = useState(false);
  // Which row's PDF is open, or null. Holding the invoice (not just its id)
  // means the modal title and filename need no second lookup.
  const [pdfFor, setPdfFor] = useState<Invoice | null>(null);
  // Which kind of document the "New quote"/"New invoice" button opened the
  // modal for -- same form either way, just a different starting status
  // (and title) so quotes go through the Quote -> Pro Forma -> Invoice
  // conversion flow on the detail page instead of starting as a real
  // invoice.
  const [modalKind, setModalKind] = useState<"quote" | "invoice">("quote");
  const [saving, setSaving] = useState(false);
  const [customer, setCustomer] = useState("");
  const [dateDue, setDateDue] = useState(new Date().toISOString().slice(0, 10));
  const [lineItems, setLineItems] = useState<LineItem[]>([{ ...BLANK_LINE_ITEM }]);
  const [products, setProducts] = useState<Product[]>([]);
  const [tariffs, setTariffs] = useState<Tariff[]>([]);

  useEffect(() => {
    api.get<{ results: Customer[] }>("/customers/?page_size=200&ordering=full_name").then((res) => setCustomers(res.data.results));
    // Stock and tariff plans, used by the "Stock item" / "Tariff plan" line
    // item types below so quotes/invoices can be built off real records
    // instead of only free-text lines.
    api.get<{ results: Product[] }>("/products/?page_size=200&ordering=name").then((res) => setProducts(res.data.results));
    api.get<{ results: Tariff[] }>("/tariffs/?page_size=200&ordering=name&is_active=true").then((res) => setTariffs(res.data.results));
  }, []);

  function updateItem(idx: number, patch: Partial<LineItem>) {
    setLineItems((prev) => prev.map((it, i) => (i === idx ? { ...it, ...patch } : it)));
  }

  function setItemType(idx: number, itemType: InvoiceItemType) {
    // Switching type starts that row's product/tariff-specific fields
    // fresh rather than carrying over a stale selection or price.
    updateItem(idx, {
      itemType,
      product: "",
      tariff: "",
      description: "",
      unit_price: "",
      tax_rate_pct: "15",
      period_start: "",
      period_end: "",
    });
  }

  function selectProduct(idx: number, productId: string) {
    const p = products.find((pr) => String(pr.id) === productId);
    updateItem(idx, { product: productId, description: p ? p.name : "" });
  }

  function selectTariff(idx: number, tariffId: string) {
    const t = tariffs.find((tr) => String(tr.id) === tariffId);
    updateItem(idx, {
      tariff: tariffId,
      description: t ? `${t.name} (${t.billing_period})` : "",
      unit_price: t ? t.price : "",
      tax_rate_pct: t ? t.tax_rate_pct : "15",
    });
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await api.post("/invoices/", {
        customer,
        date_due: dateDue,
        status: modalKind === "quote" ? "quote" : "unpaid",
        items: lineItems.map((it) => ({
          item_type: it.itemType,
          product: it.itemType === "product" ? Number(it.product) : undefined,
          tariff: it.itemType === "tariff" ? Number(it.tariff) : undefined,
          description: it.description,
          quantity: it.quantity,
          unit_price: it.unit_price,
          tax_rate_pct: it.tax_rate_pct,
          period_start: it.itemType === "tariff" ? it.period_start : undefined,
          period_end: it.itemType === "tariff" ? it.period_end : undefined,
        })),
      });
      setShowModal(false);
      setLineItems([{ ...BLANK_LINE_ITEM }]);
      refetch();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Invoices"
        subtitle={`${count} document${count === 1 ? "" : "s"} matching current filter (quotes, pro formas, and invoices)`}
        actions={
          <>
            <button
              className={btnSecondary}
              onClick={() => {
                setModalKind("quote");
                setShowModal(true);
              }}
            >
              + New quote
            </button>
            <button
              className={btnPrimary}
              onClick={() => {
                setModalKind("invoice");
                setShowModal(true);
              }}
            >
              + New invoice
            </button>
          </>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {DATE_FILTER_TABS.map((tab) => (
          <button
            key={tab.mode}
            type="button"
            onClick={() => setDateFilterMode(tab.mode)}
            className={
              dateFilterMode === tab.mode
                ? "rounded-md bg-[var(--series-1)] px-3 py-1.5 text-sm font-medium text-white"
                : "rounded-md border border-[var(--baseline)] px-3 py-1.5 text-sm font-medium text-[var(--text-primary)] hover:bg-[var(--tint-hover)]"
            }
          >
            {tab.label}
          </button>
        ))}
        <select className={filterSelectClass} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          <option value="quote">Quote</option>
          <option value="proforma">Pro forma</option>
          <option value="draft">Draft</option>
          <option value="unpaid">Unpaid</option>
          <option value="paid">Paid</option>
          <option value="overdue">Overdue</option>
          <option value="cancelled">Cancelled</option>
        </select>
        {statusFilter && (
          <button type="button" className={btnSecondary} onClick={() => setStatusFilter("")}>
            Clear status
          </button>
        )}
        <div className="ml-auto">
          <ColumnToggle columns={COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["number"]} />
        </div>
      </div>

      {dateFilterMode === "custom" && (
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <FormField label="Invoice date from">
            <input
              type="date"
              className={inputClass}
              value={customFrom}
              onChange={(e) => setCustomFrom(e.target.value)}
            />
          </FormField>
          <FormField label="Invoice date to">
            <input
              type="date"
              className={inputClass}
              value={customTo}
              onChange={(e) => setCustomTo(e.target.value)}
            />
          </FormField>
          {(customFrom || customTo) && (
            <button
              type="button"
              className={`${btnSecondary} mb-3`}
              onClick={() => {
                setCustomFrom("");
                setCustomTo("");
              }}
            >
              Clear
            </button>
          )}
        </div>
      )}

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <SortableTH field="number" ordering={ordering} onSort={toggleSort}>Number</SortableTH>
              {isVisible("customer") && <SortableTH field="customer__full_name" ordering={ordering} onSort={toggleSort}>Customer</SortableTH>}
              {isVisible("due_date") && <SortableTH field="date_due" ordering={ordering} onSort={toggleSort}>Due date</SortableTH>}
              {isVisible("total") && <SortableTH field="total" ordering={ordering} onSort={toggleSort}>Total</SortableTH>}
              {isVisible("paid") && <SortableTH field="paid_amount" ordering={ordering} onSort={toggleSort}>Paid</SortableTH>}
              {isVisible("status") && <SortableTH field="status" ordering={ordering} onSort={toggleSort}>Status</SortableTH>}
              {isVisible("preview") && <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">Preview</th>}
            </tr>
          </THead>
          <tbody>
            {items.map((inv) => (
              <TR key={inv.id}>
                <TD>
                  <Link to={`/admin/invoices/${inv.id}`} className="font-medium text-[var(--series-1)] hover:underline">
                    {inv.number}
                  </Link>
                </TD>
                {isVisible("customer") && <TD>{inv.customer_name}</TD>}
                {isVisible("due_date") && <TD>{inv.date_due}</TD>}
                {isVisible("total") && <TD className="tabular-nums">R {parseFloat(inv.total).toFixed(2)}</TD>}
                {isVisible("paid") && <TD className="tabular-nums">R {parseFloat(inv.paid_amount).toFixed(2)}</TD>}
                {isVisible("status") && <TD><StatusBadge status={inv.status} /></TD>}
                {isVisible("preview") && (
                  <TD>
                    <button
                      type="button"
                      className="text-sm font-medium text-[var(--series-1)] hover:underline"
                      onClick={() => setPdfFor(inv)}
                    >
                      Preview
                    </button>
                  </TD>
                )}
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {pdfFor && (
        <PdfPreviewModal
          title={`${pdfFor.number} — ${docNounFor(pdfFor.status)}`}
          url={`/invoices/${pdfFor.id}/pdf/`}
          filename={`${pdfFor.number}.pdf`}
          onClose={() => setPdfFor(null)}
        />
      )}

      {showModal && (
        <Modal title={modalKind === "quote" ? "New quote" : "New invoice"} onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Customer">
              <select className={inputClass} required value={customer} onChange={(e) => setCustomer(e.target.value)}>
                <option value="">Select customer…</option>
                {customers.map((c) => (
                  <option key={c.id} value={c.id}>{c.full_name} ({c.customer_id})</option>
                ))}
              </select>
            </FormField>
            <FormField label={modalKind === "quote" ? "Valid until" : "Due date"}>
              <input type="date" className={inputClass} required value={dateDue} onChange={(e) => setDateDue(e.target.value)} />
            </FormField>

            <p className="mb-2 text-sm font-medium text-[var(--text-secondary)]">Line items</p>
            {lineItems.map((item, idx) => (
              <div key={idx} className="mb-3 rounded-md border border-[var(--border-hairline)] p-3">
                <div className="mb-2 grid grid-cols-[130px_1fr] gap-2">
                  <select
                    className={inputClass}
                    value={item.itemType}
                    onChange={(e) => setItemType(idx, e.target.value as InvoiceItemType)}
                  >
                    <option value="custom">Custom</option>
                    <option value="product">Stock item</option>
                    <option value="tariff">Tariff plan</option>
                  </select>

                  {item.itemType === "product" ? (
                    <select
                      className={inputClass}
                      required
                      value={item.product}
                      onChange={(e) => selectProduct(idx, e.target.value)}
                    >
                      <option value="">Select stock item…</option>
                      {products.map((p) => (
                        <option key={p.id} value={p.id}>{p.name}{p.sku ? ` (${p.sku})` : ""}</option>
                      ))}
                    </select>
                  ) : item.itemType === "tariff" ? (
                    <select
                      className={inputClass}
                      required
                      value={item.tariff}
                      onChange={(e) => selectTariff(idx, e.target.value)}
                    >
                      <option value="">Select tariff plan…</option>
                      {tariffs.map((t) => (
                        <option key={t.id} value={t.id}>{t.name} — R {parseFloat(t.price).toFixed(2)}/{t.billing_period}</option>
                      ))}
                    </select>
                  ) : (
                    <input
                      className={inputClass}
                      placeholder="Description"
                      required
                      value={item.description}
                      onChange={(e) => updateItem(idx, { description: e.target.value })}
                    />
                  )}
                </div>

                {item.itemType !== "custom" && (
                  <input
                    className={`${inputClass} mb-2`}
                    placeholder="Description shown on the document"
                    required
                    value={item.description}
                    onChange={(e) => updateItem(idx, { description: e.target.value })}
                  />
                )}

                <div className="grid grid-cols-2 gap-2">
                  <input
                    className={inputClass}
                    placeholder="Qty"
                    type="number"
                    value={item.quantity}
                    onChange={(e) => updateItem(idx, { quantity: e.target.value })}
                  />
                  <input
                    className={inputClass}
                    placeholder="Price"
                    type="number"
                    step="0.01"
                    required
                    value={item.unit_price}
                    onChange={(e) => updateItem(idx, { unit_price: e.target.value })}
                  />
                </div>

                {item.itemType === "tariff" && (
                  <div className="mt-2 grid grid-cols-2 gap-2">
                    <FormField label="From">
                      <input
                        type="date"
                        className={inputClass}
                        required
                        value={item.period_start}
                        onChange={(e) => updateItem(idx, { period_start: e.target.value })}
                      />
                    </FormField>
                    <FormField label="Till">
                      <input
                        type="date"
                        className={inputClass}
                        required
                        value={item.period_end}
                        onChange={(e) => updateItem(idx, { period_end: e.target.value })}
                      />
                    </FormField>
                  </div>
                )}
              </div>
            ))}
            <button
              type="button"
              className="mb-4 text-xs font-medium text-[var(--series-1)] hover:underline"
              onClick={() => setLineItems([...lineItems, { ...BLANK_LINE_ITEM }])}
            >
              + Add line item
            </button>

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>Cancel</button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : modalKind === "quote" ? "Create quote" : "Create invoice"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
