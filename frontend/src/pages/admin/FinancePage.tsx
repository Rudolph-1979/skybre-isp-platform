import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, SortableTH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, filterSelectClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { ColumnToggle, type ColumnDef } from "../../components/ColumnToggle";
import { useColumnVisibility } from "../../hooks/useColumnVisibility";
import type {
  Invoice, Customer, Product, Tariff, InvoiceItemType, Payment, CreditRequest, InvoiceDeletionRequest,
  Partner, RecurringBillingCounts, RecurringBillingRun, SuspensionSettingsConfig,
  BankAccount, BankTransaction, BankTransactionStatus, BankFeedSyncLog,
  BankStatementImportPreview, BankStatementImportResult,
} from "../../types";

type Tab = "invoices" | "payments" | "credits" | "recurring-billing" | "bank-feeds";

// Unlike the single-button NewAction used elsewhere (Networking, Staff),
// Invoices needs to register two buttons at once ("+ New quote" and
// "+ New invoice"), so this is an array of buttons -- PageHeader's
// `actions` prop happily accepts any ReactNode, including several buttons.
type ActionButton = { label: string; onClick: () => void; variant?: "primary" | "secondary" };
type NewAction = ActionButton[] | null;

export function FinancePage() {
  const { user } = useAuth();
  const [tab, setTab] = useState<Tab>("invoices");
  const [newAction, setNewAction] = useState<NewAction>(null);

  const canSeeCredits = user?.role === "admin" || user?.role === "accounts" || user?.role === "management";
  const isAdmin = user?.role === "admin";

  const TABS: { key: Tab; label: string }[] = [
    { key: "invoices", label: "Invoices" },
    { key: "payments", label: "Payments" },
    ...(canSeeCredits ? [{ key: "credits" as Tab, label: "Credits" }] : []),
    { key: "recurring-billing", label: "Recurring Billing" },
    { key: "bank-feeds", label: "Bank Feeds" },
  ];

  return (
    <div>
      <PageHeader
        title="Finance"
        subtitle="Quotes, invoices, payments, and customer credit requests."
        actions={
          newAction && (
            <>
              {newAction.map((a) => (
                <button key={a.label} className={a.variant === "secondary" ? btnSecondary : btnPrimary} onClick={a.onClick}>
                  {a.label}
                </button>
              ))}
            </>
          )
        }
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
      {tab === "invoices" && <InvoicesTab onRegisterNewAction={setNewAction} />}
      {tab === "payments" && <PaymentsTab onRegisterNewAction={setNewAction} />}
      {tab === "credits" && canSeeCredits && <CreditsTab onRegisterNewAction={setNewAction} />}
      {tab === "recurring-billing" && <RecurringBillingTab onRegisterNewAction={setNewAction} />}
      {tab === "bank-feeds" && <BankFeedsTab onRegisterNewAction={setNewAction} isAdmin={isAdmin} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Invoices (quotes / pro formas / invoices)
// ---------------------------------------------------------------------------

const INVOICE_COLUMNS: ColumnDef[] = [
  { key: "number", label: "Number" },
  { key: "customer", label: "Customer" },
  { key: "due_date", label: "Due date" },
  { key: "total", label: "Total" },
  { key: "paid", label: "Paid" },
  { key: "status", label: "Status" },
];

interface LineItem {
  itemType: InvoiceItemType;
  product: string;
  tariff: string;
  description: string;
  quantity: string;
  unit_price: string;
  tax_rate_pct: string;
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

// Management-only oversight of pending quote/pro-forma deletion requests --
// deleting one of these needs a Management (or Admin) sign-off before it
// actually happens. See billing.InvoiceDeletionRequest on the backend and
// the per-document request UI on InvoiceDetailPage. Mirrors
// CustomersPage's PendingDeletionRequestsPanel for customer deletions.
function PendingInvoiceDeletionRequestsPanel() {
  const { items, loading, refetch } = useApiList<InvoiceDeletionRequest>(
    "/invoice-deletion-requests/?status=pending&page_size=50"
  );
  const [busyId, setBusyId] = useState<number | null>(null);
  const [rejecting, setRejecting] = useState<InvoiceDeletionRequest | null>(null);
  const [decisionNote, setDecisionNote] = useState("");

  if (loading || items.length === 0) return null;

  async function handleApprove(reqItem: InvoiceDeletionRequest) {
    if (!confirm(`Permanently delete ${reqItem.invoice_number}? This can't be undone.`)) return;
    setBusyId(reqItem.id);
    try {
      await api.post(`/invoice-deletion-requests/${reqItem.id}/approve/`);
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
      await api.post(`/invoice-deletion-requests/${rejecting.id}/reject/`, { decision_note: decisionNote });
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
        {items.length} pending quote/pro forma deletion request{items.length === 1 ? "" : "s"}
      </p>
      <ul className="space-y-2">
        {items.map((r) => (
          <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <div>
              <Link to={`/admin/finance/invoices/${r.invoice}`} className="font-medium text-[var(--series-1)] hover:underline">
                {r.invoice_number}
              </Link>
              <span className="ml-2 text-[var(--text-muted)]">
                {r.customer_name} — {r.reason} — requested by {r.requested_by_name ?? "a staff member"}
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
              Rejecting the deletion request for {rejecting.invoice_number}. It'll remain as-is.
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

function InvoicesTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const { user } = useAuth();
  const canDecideDeletion = user?.role === "admin" || user?.role === "management";
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
    api.get<{ results: Product[] }>("/products/?page_size=200&ordering=name").then((res) => setProducts(res.data.results));
    api.get<{ results: Tariff[] }>("/tariffs/?page_size=200&ordering=name&is_active=true").then((res) => setTariffs(res.data.results));
  }, []);

  useEffect(() => {
    onRegisterNewAction([
      {
        label: "+ New quote",
        variant: "secondary",
        onClick: () => {
          setModalKind("quote");
          setShowModal(true);
        },
      },
      {
        label: "+ New invoice",
        onClick: () => {
          setModalKind("invoice");
          setShowModal(true);
        },
      },
    ]);
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function updateItem(idx: number, patch: Partial<LineItem>) {
    setLineItems((prev) => prev.map((it, i) => (i === idx ? { ...it, ...patch } : it)));
  }

  function setItemType(idx: number, itemType: InvoiceItemType) {
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
      {canDecideDeletion && <PendingInvoiceDeletionRequestsPanel />}

      <p className="mb-3 text-sm text-[var(--text-secondary)]">
        {count} document{count === 1 ? "" : "s"} matching current filter (quotes, pro formas, and invoices)
      </p>

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
          <ColumnToggle columns={INVOICE_COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["number"]} />
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
            </tr>
          </THead>
          <tbody>
            {items.map((inv) => (
              <TR key={inv.id}>
                <TD>
                  <Link to={`/admin/finance/invoices/${inv.id}`} className="font-medium text-[var(--series-1)] hover:underline">
                    {inv.number}
                  </Link>
                </TD>
                {isVisible("customer") && <TD>{inv.customer_name}</TD>}
                {isVisible("due_date") && <TD>{inv.date_due}</TD>}
                {isVisible("total") && <TD className="tabular-nums">R {parseFloat(inv.total).toFixed(2)}</TD>}
                {isVisible("paid") && <TD className="tabular-nums">R {parseFloat(inv.paid_amount).toFixed(2)}</TD>}
                {isVisible("status") && <TD><StatusBadge status={inv.status} /></TD>}
              </TR>
            ))}
          </tbody>
        </Table>
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

// ---------------------------------------------------------------------------
// Payments
// ---------------------------------------------------------------------------

const PAYMENT_COLUMNS: ColumnDef[] = [
  { key: "date", label: "Date" },
  { key: "customer", label: "Customer" },
  { key: "invoice", label: "Invoice" },
  { key: "amount", label: "Amount" },
  { key: "method", label: "Method" },
  { key: "received_by", label: "Received by" },
];

function PaymentsTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const [ordering, setOrdering] = useState("-date");
  const [methodFilter, setMethodFilter] = useState("");
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("payments", ["date"]);
  const { items, count, loading } = useApiList<Payment>(
    `/payments/?page_size=100&ordering=${ordering}${methodFilter ? `&method=${methodFilter}` : ""}`
  );

  function toggleSort(field: string) {
    setOrdering((prev) => (prev === field ? `-${field}` : prev === `-${field}` ? "-date" : field));
  }

  useEffect(() => {
    // Payments have no create button of their own -- they're recorded via
    // the "Record payment" modal on an invoice's detail page.
    onRegisterNewAction(null);
  }, [onRegisterNewAction]);

  return (
    <div>
      <p className="mb-3 text-sm text-[var(--text-secondary)]">{count} payments recorded</p>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select className={filterSelectClass} value={methodFilter} onChange={(e) => setMethodFilter(e.target.value)}>
          <option value="">All methods</option>
          <option value="cash">Cash</option>
          <option value="card">Card</option>
          <option value="bank_transfer">Bank transfer</option>
          <option value="mobile_money">Mobile money</option>
          <option value="manual">Manual</option>
        </select>
        {methodFilter && (
          <button type="button" className={btnSecondary} onClick={() => setMethodFilter("")}>
            Clear filters
          </button>
        )}
        <div className="ml-auto">
          <ColumnToggle columns={PAYMENT_COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["date"]} />
        </div>
      </div>
      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <SortableTH field="date" ordering={ordering} onSort={toggleSort}>Date</SortableTH>
              {isVisible("customer") && <SortableTH field="customer__full_name" ordering={ordering} onSort={toggleSort}>Customer</SortableTH>}
              {isVisible("invoice") && <TH>Invoice</TH>}
              {isVisible("amount") && <SortableTH field="amount" ordering={ordering} onSort={toggleSort}>Amount</SortableTH>}
              {isVisible("method") && <SortableTH field="method" ordering={ordering} onSort={toggleSort}>Method</SortableTH>}
              {isVisible("received_by") && <SortableTH field="received_by__username" ordering={ordering} onSort={toggleSort}>Received by</SortableTH>}
            </tr>
          </THead>
          <tbody>
            {items.map((p) => (
              <TR key={p.id}>
                <TD>{new Date(p.date).toLocaleString()}</TD>
                {isVisible("customer") && <TD>{p.customer_name}</TD>}
                {isVisible("invoice") && <TD>{p.invoice ?? "—"}</TD>}
                {isVisible("amount") && <TD className="tabular-nums">R {parseFloat(p.amount).toFixed(2)}</TD>}
                {isVisible("method") && <TD className="capitalize">{p.method.replace("_", " ")}</TD>}
                {isVisible("received_by") && <TD>{p.received_by_name ?? "—"}</TD>}
              </TR>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Credits -- customer credit requests, submitted by Accounts, approved or
// rejected by Management (Admin can always do both, per the app-wide
// convention that Admin overrides every senior-role gate).
// ---------------------------------------------------------------------------

const EMPTY_CREDIT = { customer: "", amount: "", reason: "" };

function CreditsTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const { user } = useAuth();
  const canSubmit = user?.role === "admin" || user?.role === "accounts";
  const canDecide = user?.role === "admin" || user?.role === "management";

  const [customerFilter, setCustomerFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const { items, count, loading, refetch } = useApiList<CreditRequest>(
    `/credit-requests/?page_size=200${customerFilter ? `&customer=${customerFilter}` : ""}${
      statusFilter ? `&status=${statusFilter}` : ""
    }`
  );

  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState(EMPTY_CREDIT);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [rejecting, setRejecting] = useState<CreditRequest | null>(null);
  const [decisionNote, setDecisionNote] = useState("");

  useEffect(() => {
    api.get<{ results: Customer[] }>("/customers/?page_size=200&ordering=full_name").then((res) => setCustomers(res.data.results));
  }, []);

  useEffect(() => {
    if (!canSubmit) {
      onRegisterNewAction(null);
      return;
    }
    onRegisterNewAction([
      {
        label: "+ Request credit",
        onClick: () => {
          setForm(EMPTY_CREDIT);
          setError("");
          setShowModal(true);
        },
      },
    ]);
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canSubmit]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      await api.post("/credit-requests/", {
        customer: Number(form.customer),
        amount: form.amount,
        reason: form.reason,
      });
      setShowModal(false);
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: unknown } })?.response?.data;
      setError(
        typeof detail === "string"
          ? detail
          : (detail as { amount?: string[]; reason?: string[]; non_field_errors?: string[] })?.amount?.[0] ||
              (detail as { reason?: string[] })?.reason?.[0] ||
              (detail as { non_field_errors?: string[] })?.non_field_errors?.[0] ||
              "Failed to submit credit request."
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleApprove(credit: CreditRequest) {
    if (
      !confirm(
        `Approve a credit of R ${parseFloat(credit.amount).toFixed(2)} for ${credit.customer_name}? This will reduce their balance by that amount.`
      )
    )
      return;
    setBusyId(credit.id);
    try {
      await api.post(`/credit-requests/${credit.id}/approve/`);
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      alert(detail || "Failed to approve this credit request.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleReject(e: FormEvent) {
    e.preventDefault();
    if (!rejecting) return;
    setBusyId(rejecting.id);
    try {
      await api.post(`/credit-requests/${rejecting.id}/reject/`, { decision_note: decisionNote });
      setRejecting(null);
      setDecisionNote("");
      refetch();
    } finally {
      setBusyId(null);
    }
  }

  async function handleWithdraw(credit: CreditRequest) {
    if (!confirm(`Withdraw this credit request for ${credit.customer_name}?`)) return;
    await api.delete(`/credit-requests/${credit.id}/`);
    refetch();
  }

  return (
    <div>
      <p className="mb-3 text-sm text-[var(--text-secondary)]">{count} credit request{count === 1 ? "" : "s"}</p>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select className={filterSelectClass} value={customerFilter} onChange={(e) => setCustomerFilter(e.target.value)}>
          <option value="">All customers</option>
          {customers.map((c) => (
            <option key={c.id} value={c.id}>{c.full_name} ({c.customer_id})</option>
          ))}
        </select>
        <select className={filterSelectClass} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          <option value="pending">Pending</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
        </select>
        {(customerFilter || statusFilter) && (
          <button
            type="button"
            className={btnSecondary}
            onClick={() => {
              setCustomerFilter("");
              setStatusFilter("");
            }}
          >
            Clear filters
          </button>
        )}
      </div>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Customer</TH>
              <TH>Amount</TH>
              <TH>Reason</TH>
              <TH>Status</TH>
              <TH>Requested by</TH>
              <TH>Decided by</TH>
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((c) => (
              <TR key={c.id}>
                <TD className="font-medium">{c.customer_name}</TD>
                <TD className="tabular-nums">R {parseFloat(c.amount).toFixed(2)}</TD>
                <TD>{c.reason}</TD>
                <TD>
                  <StatusBadge status={c.status} />
                  {c.status === "rejected" && c.decision_note && (
                    <div className="mt-1 text-xs text-[var(--text-muted)]">{c.decision_note}</div>
                  )}
                </TD>
                <TD>{c.requested_by_name || "—"}</TD>
                <TD>{c.decided_by_name || "—"}</TD>
                <TD>
                  {c.status === "pending" && canDecide && (
                    <>
                      <button
                        className="text-xs text-[var(--series-1)] hover:underline"
                        disabled={busyId === c.id}
                        onClick={() => handleApprove(c)}
                      >
                        Approve
                      </button>
                      <button
                        className="ml-2 text-xs text-[var(--status-critical)] hover:underline"
                        disabled={busyId === c.id}
                        onClick={() => {
                          setRejecting(c);
                          setDecisionNote("");
                        }}
                      >
                        Reject
                      </button>
                    </>
                  )}
                  {c.status === "pending" && canSubmit && (
                    <button
                      className="ml-2 text-xs text-[var(--status-critical)] hover:underline"
                      onClick={() => handleWithdraw(c)}
                    >
                      Withdraw
                    </button>
                  )}
                </TD>
              </TR>
            ))}
            {items.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]">No credit requests match the current filters.</TD>
              </TR>
            )}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title="Request credit" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Customer">
              <select className={inputClass} required value={form.customer} onChange={(e) => setForm({ ...form, customer: e.target.value })}>
                <option value="">Select customer…</option>
                {customers.map((c) => (
                  <option key={c.id} value={c.id}>{c.full_name} ({c.customer_id})</option>
                ))}
              </select>
            </FormField>
            <FormField label="Amount (R)">
              <input
                type="number"
                step="0.01"
                min="0.01"
                className={inputClass}
                required
                value={form.amount}
                onChange={(e) => setForm({ ...form, amount: e.target.value })}
              />
            </FormField>
            <FormField label="Reason">
              <input
                className={inputClass}
                required
                value={form.reason}
                onChange={(e) => setForm({ ...form, reason: e.target.value })}
              />
            </FormField>
            <p className="mb-3 text-xs text-[var(--text-muted)]">
              Once approved by Management, this amount is deducted directly from the customer's balance.
            </p>
            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>Cancel</button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Submitting…" : "Submit request"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {rejecting && (
        <Modal title="Reject credit request" onClose={() => setRejecting(null)}>
          <form onSubmit={handleReject}>
            <p className="mb-3 text-sm text-[var(--text-secondary)]">
              Rejecting a R {parseFloat(rejecting.amount).toFixed(2)} credit request for {rejecting.customer_name}.
            </p>
            <FormField label="Reason (optional)">
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

// ---------------------------------------------------------------------------
// Recurring Billing -- Preview/Run the engine for a date (+ optional
// partner scope), and a History of past committed Runs. See
// billing.recurring.run_recurring_billing on the backend; a Preview never
// writes anything (not even a History row) -- only a Run does.
// ---------------------------------------------------------------------------

function RecurringBillingTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const { items: partners } = useApiList<Partner>("/partners/?page_size=200&ordering=name");
  const { items: runs, loading: runsLoading, refetch: refetchRuns } = useApiList<RecurringBillingRun>(
    "/recurring-billing-runs/?page_size=50&ordering=-created_at"
  );

  // Read-only glance at the global auto-suspension master switch (Configs ->
  // Billing -> Auto-suspension) -- purely informational here, so staff know
  // before clicking Run whether it's even possible for this cycle to
  // suspend anyone. Fetched once; the actual switch is only ever flipped
  // from Configs.
  const [suspensionSettings, setSuspensionSettings] = useState<SuspensionSettingsConfig | null>(null);
  useEffect(() => {
    api.get<SuspensionSettingsConfig>("/suspension-settings/").then((r) => setSuspensionSettings(r.data)).catch(() => {});
  }, []);

  const [runDate, setRunDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [partnersRestricted, setPartnersRestricted] = useState(false);
  const [checkedPartners, setCheckedPartners] = useState<Set<number>>(new Set());

  const [previewCounts, setPreviewCounts] = useState<RecurringBillingCounts | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState("");

  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState("");
  const [confirmingRun, setConfirmingRun] = useState(false);

  useEffect(() => {
    onRegisterNewAction(null);
  }, [onRegisterNewAction]);

  function togglePartner(id: number) {
    setCheckedPartners((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function partnerPayload(): number[] {
    return partnersRestricted ? Array.from(checkedPartners) : [];
  }

  async function handlePreview() {
    setPreviewing(true);
    setPreviewError("");
    setPreviewCounts(null);
    try {
      const res = await api.post<{ counts: RecurringBillingCounts }>("/recurring-billing/preview/", {
        date: runDate,
        partners: partnerPayload(),
      });
      setPreviewCounts(res.data.counts);
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setPreviewError(detail || "Could not generate a preview for this date.");
    } finally {
      setPreviewing(false);
    }
  }

  async function handleRun() {
    setRunning(true);
    setRunError("");
    try {
      await api.post<RecurringBillingRun>("/recurring-billing/run/", { date: runDate, partners: partnerPayload() });
      setPreviewCounts(null);
      setConfirmingRun(false);
      refetchRuns();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setRunError(detail || "Could not run recurring billing for this date.");
      setConfirmingRun(false);
    } finally {
      setRunning(false);
    }
  }

  return (
    <div>
      {suspensionSettings && !suspensionSettings.auto_suspend_enabled && (
        <p className="mb-3 max-w-2xl rounded-md border border-[var(--border-hairline)] bg-[var(--surface-2)] px-3 py-2 text-sm text-[var(--text-secondary)]">
          Auto-suspension is currently <strong>OFF</strong> platform-wide — Run will never suspend anyone this
          cycle, even for customers whose blocking period has passed. Change this under{" "}
          <Link to="/admin/configs" className="underline">
            Configs → Billing → Auto-suspension
          </Link>
          .
        </p>
      )}
      {suspensionSettings?.auto_suspend_enabled && (
        <p className="mb-3 max-w-2xl rounded-md border border-[var(--status-critical)] bg-[var(--surface-2)] px-3 py-2 text-sm text-[var(--text-secondary)]">
          Auto-suspension is currently <strong className="text-[var(--status-critical)]">ON</strong> platform-wide —
          Run can suspend real, overdue customers this cycle.
        </p>
      )}
      <p className="mb-4 max-w-2xl text-sm text-[var(--text-secondary)]">
        Preview shows what would happen for a given date without writing anything. Run actually creates the
        invoices/pro formas, sends reminder and billing emails, applies auto-suspensions, and logs a row below —
        staff trigger each cycle manually for now; nothing runs unattended yet.
      </p>

      <div className="mb-6 max-w-2xl rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <FormField label="Date">
            <input type="date" className={inputClass} value={runDate} onChange={(e) => setRunDate(e.target.value)} />
          </FormField>
          <button type="button" className={btnSecondary} disabled={previewing} onClick={handlePreview}>
            {previewing ? "Loading…" : "Preview"}
          </button>
          {!confirmingRun ? (
            <button type="button" className={btnPrimary} onClick={() => setConfirmingRun(true)}>
              Run…
            </button>
          ) : (
            <>
              <button type="button" className={btnSecondary} onClick={() => setConfirmingRun(false)}>
                Cancel
              </button>
              <button type="button" className={btnPrimary} disabled={running} onClick={handleRun}>
                {running ? "Running…" : "Confirm run"}
              </button>
            </>
          )}
        </div>

        {partners.length > 0 && (
          <div className="mb-4">
            <label className="mb-2 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={partnersRestricted} onChange={(e) => setPartnersRestricted(e.target.checked)} />
              <span className="font-medium text-[var(--text-secondary)]">Scope to specific partners</span>
            </label>
            <div className={`grid grid-cols-1 gap-2 sm:grid-cols-3 ${partnersRestricted ? "" : "opacity-40"}`}>
              {partners.map((p) => (
                <label key={p.id} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    disabled={!partnersRestricted}
                    checked={checkedPartners.has(p.id)}
                    onChange={() => togglePartner(p.id)}
                  />
                  <span>{p.name}</span>
                </label>
              ))}
            </div>
          </div>
        )}

        {previewError && <p className="mb-2 text-sm text-[var(--status-critical)]">{previewError}</p>}
        {runError && <p className="mb-2 text-sm text-[var(--status-critical)]">{runError}</p>}

        {previewCounts && (
          <div className="grid grid-cols-2 gap-3 rounded-md border border-[var(--border-hairline)] bg-[var(--tint-subtle)] p-4 text-sm sm:grid-cols-4">
            <div><p className="text-[var(--text-muted)]">Invoices</p><p className="text-lg font-semibold">{previewCounts.invoices_created}</p></div>
            <div><p className="text-[var(--text-muted)]">Pro formas</p><p className="text-lg font-semibold">{previewCounts.proforma_invoices_created}</p></div>
            <div><p className="text-[var(--text-muted)]">Reminders</p><p className="text-lg font-semibold">{previewCounts.reminders_sent}</p></div>
            <div><p className="text-[var(--text-muted)]">Suspensions</p><p className="text-lg font-semibold">{previewCounts.suspensions_applied}</p></div>
          </div>
        )}
      </div>

      <h2 className="mb-2 text-sm font-semibold">History</h2>
      {runsLoading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Date</TH>
              <TH>Partners</TH>
              <TH>Status</TH>
              <TH>Invoices</TH>
              <TH>Pro formas</TH>
              <TH>Reminders</TH>
              <TH>Suspensions</TH>
              <TH>Triggered by</TH>
              <TH>Run at</TH>
            </tr>
          </THead>
          <tbody>
            {runs.map((r) => (
              <TR key={r.id}>
                <TD>{r.run_date}</TD>
                <TD>{r.partner_names}</TD>
                <TD>
                  <StatusBadge status={r.status === "processed" ? "active" : "failed"} />
                  {r.status === "failed" && r.status_message && (
                    <span className="ml-2 text-xs text-[var(--text-muted)]">{r.status_message}</span>
                  )}
                </TD>
                <TD>{r.invoices_created_count}</TD>
                <TD>{r.proforma_invoices_created_count}</TD>
                <TD>{r.reminders_sent_count}</TD>
                <TD>{r.suspensions_applied_count}</TD>
                <TD>{r.triggered_by_name ?? "—"}</TD>
                <TD>{new Date(r.created_at).toLocaleString()}</TD>
              </TR>
            ))}
            {runs.length === 0 && <TR><TD className="text-[var(--text-muted)]">No recurring-billing runs logged yet.</TD></TR>}
          </tbody>
        </Table>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bank Feeds -- FNB accounts (Admin-only), the transaction review queue
// (assign/confirm/ignore/unmatch, CSV import), and sync History. Direct
// API access to FNB isn't confirmed yet (see the backend's fnb_client.py
// docstring) -- CSV import is the practical way to use this today; the
// same review queue and every action works identically regardless of
// which source a transaction came from.
// ---------------------------------------------------------------------------

type BankFeedsSubTab = "review" | "accounts" | "history";

function BankFeedsTab({ onRegisterNewAction, isAdmin }: { onRegisterNewAction: (action: NewAction) => void; isAdmin: boolean }) {
  const [subTab, setSubTab] = useState<BankFeedsSubTab>("review");

  const SUB_TABS: { key: BankFeedsSubTab; label: string }[] = [
    { key: "review", label: "Review" },
    ...(isAdmin ? [{ key: "accounts" as BankFeedsSubTab, label: "Accounts" }] : []),
    { key: "history", label: "History" },
  ];

  return (
    <div>
      <div className="mb-4 flex gap-1 border-b border-[var(--border-hairline)]">
        {SUB_TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setSubTab(t.key)}
            className={`px-3 py-1.5 text-sm font-medium ${
              subTab === t.key
                ? "border-b-2 border-[var(--series-1)] text-[var(--text-primary)]"
                : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {subTab === "review" && <BankFeedsReviewSubTab onRegisterNewAction={onRegisterNewAction} />}
      {subTab === "accounts" && isAdmin && <BankFeedsAccountsSubTab onRegisterNewAction={onRegisterNewAction} />}
      {subTab === "history" && <BankFeedsHistorySubTab onRegisterNewAction={onRegisterNewAction} />}
    </div>
  );
}

// --- Accounts (Admin-only) --------------------------------------------------

const EMPTY_BANK_ACCOUNT_FORM = {
  name: "", account_number: "", branch_code: "", is_active: true,
  api_base_url: "", api_client_id: "", api_client_secret: "",
};

function BankFeedsAccountsSubTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const { items, loading, refetch } = useApiList<BankAccount>("/bank-accounts/?page_size=50&ordering=name");
  const [editing, setEditing] = useState<BankAccount | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState(EMPTY_BANK_ACCOUNT_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [syncingId, setSyncingId] = useState<number | null>(null);

  useEffect(() => {
    onRegisterNewAction([
      {
        label: "+ New account",
        onClick: () => {
          setEditing(null);
          setForm(EMPTY_BANK_ACCOUNT_FORM);
          setError("");
          setShowModal(true);
        },
      },
    ]);
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function openEdit(acc: BankAccount) {
    setEditing(acc);
    setForm({
      name: acc.name, account_number: acc.account_number, branch_code: acc.branch_code, is_active: acc.is_active,
      api_base_url: acc.api_base_url, api_client_id: acc.api_client_id, api_client_secret: "",
    });
    setError("");
    setShowModal(true);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      const payload: Record<string, unknown> = { ...form };
      if (!form.api_client_secret) delete payload.api_client_secret;
      if (editing) {
        await api.patch(`/bank-accounts/${editing.id}/`, payload);
      } else {
        await api.post("/bank-accounts/", payload);
      }
      setShowModal(false);
      refetch();
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setError(typeof firstError === "string" ? firstError : "Could not save this account — please try again.");
    } finally {
      setSaving(false);
    }
  }

  async function handleSyncNow(id: number) {
    setSyncingId(id);
    try {
      await api.post(`/bank-accounts/${id}/sync-now/`);
      refetch();
    } finally {
      setSyncingId(null);
    }
  }

  return (
    <div>
      <p className="mb-3 text-sm text-[var(--text-secondary)]">
        Up to a handful of FNB accounts to read incoming payments from. Direct API access from FNB isn't confirmed
        yet — leave "API base URL" blank and use CSV import on the Review tab in the meantime; "Sync now" only works
        once that's filled in.
      </p>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Name</TH>
              <TH>Account number</TH>
              <TH>Status</TH>
              <TH>API connection</TH>
              <TH>Last sync</TH>
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((acc) => (
              <TR key={acc.id}>
                <TD className="font-medium">{acc.name}</TD>
                <TD className="text-[var(--text-secondary)]">{acc.account_number || "—"}</TD>
                <TD><StatusBadge status={acc.is_active ? "active" : "inactive"} /></TD>
                <TD className="text-[var(--text-secondary)]">{acc.api_base_url ? "Configured" : "Not configured (CSV import only)"}</TD>
                <TD className="text-[var(--text-secondary)]">
                  {acc.last_synced_at ? (
                    <>
                      {new Date(acc.last_synced_at).toLocaleString()}
                      {acc.last_sync_status === "failed" && (
                        <span className="ml-2 text-[var(--status-critical)]" title={acc.last_sync_message}>failed</span>
                      )}
                    </>
                  ) : "Never"}
                </TD>
                <TD>
                  <div className="flex gap-2">
                    <button type="button" className={btnSecondary} disabled={syncingId === acc.id} onClick={() => handleSyncNow(acc.id)}>
                      {syncingId === acc.id ? "Syncing…" : "Sync now"}
                    </button>
                    <button type="button" className={btnSecondary} onClick={() => openEdit(acc)}>
                      Edit
                    </button>
                  </div>
                </TD>
              </TR>
            ))}
            {items.length === 0 && <TR><TD className="text-[var(--text-muted)]">No bank accounts configured yet.</TD></TR>}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title={editing ? "Edit bank account" : "New bank account"} onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit} autoComplete="off">
            <FormField label="Name">
              <input className={inputClass} required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </FormField>
            <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
              <FormField label="Account number">
                <input className={inputClass} value={form.account_number} onChange={(e) => setForm({ ...form, account_number: e.target.value })} />
              </FormField>
              <FormField label="Branch code">
                <input className={inputClass} placeholder="e.g. 250655" value={form.branch_code} onChange={(e) => setForm({ ...form, branch_code: e.target.value })} />
              </FormField>
            </div>
            <label className="mb-4 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
              <span>Active (included in the hourly sync)</span>
            </label>

            <div className="my-4 border-t border-[var(--border-hairline)]" />
            <p className="mb-3 text-xs text-[var(--text-muted)]">
              Only needed once FNB confirms direct API access for this account — leave blank to rely on CSV import.
            </p>
            <FormField label="API base URL">
              <input className={inputClass} placeholder="https://…" value={form.api_base_url} onChange={(e) => setForm({ ...form, api_base_url: e.target.value })} />
            </FormField>
            <FormField label="API client ID">
              <input
                className={inputClass}
                value={form.api_client_id}
                onChange={(e) => setForm({ ...form, api_client_id: e.target.value })}
                autoComplete="off"
                name="bank-api-client-id"
              />
            </FormField>
            <FormField label={editing?.api_client_secret_set ? "API client secret (a secret is set — leave blank to keep it)" : "API client secret"}>
              <input
                type="password"
                className={inputClass}
                value={form.api_client_secret}
                onChange={(e) => setForm({ ...form, api_client_secret: e.target.value })}
                autoComplete="new-password"
                name="bank-api-client-secret"
              />
            </FormField>

            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : editing ? "Save changes" : "Create account"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// --- Review queue ------------------------------------------------------

const BANK_STATUS_FILTERS: { key: BankTransactionStatus | ""; label: string }[] = [
  { key: "unmatched", label: "Unmatched" },
  { key: "matched", label: "Matched (awaiting confirmation)" },
  { key: "confirmed", label: "Confirmed" },
  { key: "ignored", label: "Ignored" },
  { key: "", label: "All" },
];

function BankFeedsReviewSubTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const [statusFilter, setStatusFilter] = useState<BankTransactionStatus | "">("unmatched");
  const { items, loading, refetch } = useApiList<BankTransaction>(
    `/bank-transactions/?page_size=100&ordering=-date${statusFilter ? `&status=${statusFilter}` : ""}`
  );
  const [accounts, setAccounts] = useState<BankAccount[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [selectedCustomer, setSelectedCustomer] = useState<Record<number, string>>({});
  const [busyId, setBusyId] = useState<number | null>(null);
  const [rowError, setRowError] = useState<Record<number, string>>({});
  const [showImport, setShowImport] = useState(false);

  useEffect(() => {
    api.get<{ results: BankAccount[] }>("/bank-accounts/?page_size=50&ordering=name").then((res) => setAccounts(res.data.results));
    api.get<{ results: Customer[] }>("/customers/?page_size=500&ordering=full_name").then((res) => setCustomers(res.data.results));
  }, []);

  useEffect(() => {
    onRegisterNewAction([{ label: "Import statement CSV", onClick: () => setShowImport(true) }]);
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleAssign(txn: BankTransaction) {
    const customerId = selectedCustomer[txn.id];
    if (!customerId) return;
    setBusyId(txn.id);
    setRowError((prev) => ({ ...prev, [txn.id]: "" }));
    try {
      await api.post(`/bank-transactions/${txn.id}/assign/`, { customer: customerId });
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setRowError((prev) => ({ ...prev, [txn.id]: detail || "Could not assign this customer." }));
    } finally {
      setBusyId(null);
    }
  }

  async function handleConfirm(txn: BankTransaction) {
    setBusyId(txn.id);
    setRowError((prev) => ({ ...prev, [txn.id]: "" }));
    try {
      const customerId = selectedCustomer[txn.id];
      await api.post(`/bank-transactions/${txn.id}/confirm/`, customerId ? { customer: customerId } : {});
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setRowError((prev) => ({ ...prev, [txn.id]: detail || "Could not confirm this transaction." }));
    } finally {
      setBusyId(null);
    }
  }

  async function handleIgnore(txn: BankTransaction) {
    setBusyId(txn.id);
    try {
      await api.post(`/bank-transactions/${txn.id}/ignore/`);
      refetch();
    } finally {
      setBusyId(null);
    }
  }

  async function handleUnmatch(txn: BankTransaction) {
    setBusyId(txn.id);
    try {
      await api.post(`/bank-transactions/${txn.id}/unmatch/`);
      refetch();
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div>
      <p className="mb-3 text-sm text-[var(--text-secondary)]">
        Incoming bank transactions, matched to a customer by the reference number in the description where possible.
        Nothing becomes a real Payment (or changes a customer's balance) until you click Confirm.
      </p>

      <div className="mb-4 flex flex-wrap gap-2">
        {BANK_STATUS_FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            onClick={() => setStatusFilter(f.key)}
            className={statusFilter === f.key ? btnPrimary : btnSecondary}
          >
            {f.label}
          </button>
        ))}
      </div>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Date</TH>
              <TH>Account</TH>
              <TH>Description</TH>
              <TH>Amount</TH>
              <TH>Customer</TH>
              <TH>Status</TH>
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((txn) => (
              <TR key={txn.id}>
                <TD>{txn.date}</TD>
                <TD className="text-[var(--text-secondary)]">{txn.account_name}</TD>
                <TD className="max-w-xs truncate"><span title={txn.description}>{txn.description}</span></TD>
                <TD className={`tabular-nums ${parseFloat(txn.amount) < 0 ? "text-[var(--text-muted)]" : ""}`}>
                  R {parseFloat(txn.amount).toFixed(2)}
                </TD>
                <TD>
                  {txn.status === "confirmed" || txn.status === "ignored" ? (
                    txn.matched_customer_name || <span className="text-[var(--text-muted)]">—</span>
                  ) : (
                    <select
                      className={filterSelectClass}
                      value={selectedCustomer[txn.id] ?? (txn.matched_customer ? String(txn.matched_customer) : "")}
                      onChange={(e) => setSelectedCustomer((prev) => ({ ...prev, [txn.id]: e.target.value }))}
                    >
                      <option value="">Select customer…</option>
                      {customers.map((c) => (
                        <option key={c.id} value={c.id}>{c.full_name} ({c.customer_id})</option>
                      ))}
                    </select>
                  )}
                  {txn.match_method === "reference" && txn.status === "matched" && (
                    <span className="ml-2 text-xs text-[var(--status-good)]">auto-matched</span>
                  )}
                </TD>
                <TD><StatusBadge status={txn.status === "confirmed" ? "active" : txn.status === "ignored" ? "inactive" : txn.status} /></TD>
                <TD>
                  <div className="flex flex-wrap gap-2">
                    {(txn.status === "unmatched" || txn.status === "matched") && (
                      <>
                        {(selectedCustomer[txn.id] && selectedCustomer[txn.id] !== String(txn.matched_customer)) && (
                          <button type="button" className={btnSecondary} disabled={busyId === txn.id} onClick={() => handleAssign(txn)}>
                            Assign
                          </button>
                        )}
                        <button
                          type="button" className={btnPrimary} disabled={busyId === txn.id || (!txn.matched_customer && !selectedCustomer[txn.id])}
                          onClick={() => handleConfirm(txn)}
                        >
                          Confirm
                        </button>
                        {txn.status === "matched" && (
                          <button type="button" className={btnSecondary} disabled={busyId === txn.id} onClick={() => handleUnmatch(txn)}>
                            Unmatch
                          </button>
                        )}
                        <button type="button" className={btnSecondary} disabled={busyId === txn.id} onClick={() => handleIgnore(txn)}>
                          Ignore
                        </button>
                      </>
                    )}
                  </div>
                  {rowError[txn.id] && <p className="mt-1 text-xs text-[var(--status-critical)]">{rowError[txn.id]}</p>}
                </TD>
              </TR>
            ))}
            {items.length === 0 && <TR><TD className="text-[var(--text-muted)]">No transactions in this filter.</TD></TR>}
          </tbody>
        </Table>
      )}

      {showImport && (
        <BankStatementImportModal accounts={accounts} onClose={() => setShowImport(false)} onImported={refetch} />
      )}
    </div>
  );
}

// --- CSV import modal (bespoke -- needs an account selector alongside the
// file, which the generic CSVImportModal doesn't support) --------------

function BankStatementImportModal({
  accounts, onClose, onImported,
}: {
  accounts: BankAccount[]; onClose: () => void; onImported: () => void;
}) {
  const [accountId, setAccountId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<BankStatementImportPreview | null>(null);
  const [result, setResult] = useState<BankStatementImportResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handlePreview() {
    if (!file || !accountId) return;
    setLoading(true);
    setError("");
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("account", accountId);
      const res = await api.post<BankStatementImportPreview>("/bank-transactions/import-preview/", formData);
      setPreview(res.data);
    } catch {
      setError("Could not read that file. Make sure it's a CSV with Date/Description/Amount columns.");
    } finally {
      setLoading(false);
    }
  }

  async function handleCommit() {
    if (!file || !accountId) return;
    setLoading(true);
    setError("");
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("account", accountId);
      const res = await api.post<BankStatementImportResult>("/bank-transactions/import-commit/", formData);
      setResult(res.data);
      onImported();
    } catch {
      setError("Import failed. Nothing was changed — please try again.");
    } finally {
      setLoading(false);
    }
  }

  const importableCount = preview ? preview.valid_count - preview.already_imported_count : 0;

  return (
    <Modal title="Import bank statement CSV" onClose={onClose}>
      {!result ? (
        <>
          <p className="mb-3 text-sm text-[var(--text-secondary)]">
            Expects columns Date, Description, and a single signed Amount (positive = money in, negative = money
            out). Export this from FNB Online Banking for the account below.
          </p>

          <FormField label="Bank account">
            <select className={inputClass} required value={accountId} onChange={(e) => { setAccountId(e.target.value); setPreview(null); setResult(null); }}>
              <option value="">Select account…</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
          </FormField>

          <input
            type="file"
            accept=".csv,text/csv"
            className="mb-4 block w-full text-sm"
            onChange={(e) => { setFile(e.target.files?.[0] ?? null); setPreview(null); setResult(null); }}
          />

          {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}

          {preview && (
            <div className="mb-4 max-h-64 overflow-y-auto rounded-md border border-[var(--border-hairline)] p-3 text-sm">
              <p className="mb-2 font-medium text-[var(--text-primary)]">
                {preview.total_rows} row{preview.total_rows === 1 ? "" : "s"} found —{" "}
                <span className="text-[var(--status-good)]">{importableCount} new row{importableCount === 1 ? "" : "s"} to import</span>
                {preview.already_imported_count > 0 && <>, {preview.already_imported_count} already imported</>}
                {preview.invalid_count > 0 && (
                  <>, <span className="text-[var(--status-critical)]">{preview.invalid_count} with problems</span></>
                )}
              </p>
              {preview.invalid_count > 0 && (
                <ul className="space-y-1">
                  {preview.rows.filter((r) => r.errors.length > 0).slice(0, 30).map((r) => (
                    <li key={r.row} className="text-[var(--text-secondary)]">
                      <span className="font-medium">Row {r.row}:</span> {r.errors.join("; ")}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <div className="mt-4 flex justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={onClose}>
              Cancel
            </button>
            {!preview ? (
              <button type="button" disabled={!file || !accountId || loading} className={btnPrimary} onClick={handlePreview}>
                {loading ? "Reading…" : "Preview"}
              </button>
            ) : (
              <button type="button" disabled={loading || importableCount === 0} className={btnPrimary} onClick={handleCommit}>
                {loading ? "Importing…" : `Import ${importableCount} row${importableCount === 1 ? "" : "s"}`}
              </button>
            )}
          </div>
        </>
      ) : (
        <>
          <p className="mb-3 text-sm text-[var(--text-primary)]">
            <span className="font-medium text-[var(--status-good)]">{result.created} imported</span>
            {result.matched > 0 && <>, {result.matched} auto-matched to a customer</>}
            {result.duplicates_skipped > 0 && <>, {result.duplicates_skipped} already-imported duplicates skipped</>}
            {result.invalid_skipped > 0 && (
              <span className="text-[var(--status-critical)]">, {result.invalid_skipped} skipped (see below)</span>
            )}
            .
          </p>
          {result.skipped.length > 0 && (
            <ul className="mb-4 max-h-48 space-y-1 overflow-y-auto text-sm text-[var(--text-secondary)]">
              {result.skipped.slice(0, 30).map((r) => (
                <li key={r.row}><span className="font-medium">Row {r.row}:</span> {r.errors.join("; ")}</li>
              ))}
            </ul>
          )}
          <div className="mt-4 flex justify-end">
            <button type="button" className={btnPrimary} onClick={onClose}>
              Done
            </button>
          </div>
        </>
      )}
    </Modal>
  );
}

// --- History -------------------------------------------------------------

function BankFeedsHistorySubTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const { items, loading } = useApiList<BankFeedSyncLog>("/bank-feed-sync-logs/?page_size=50&ordering=-created_at");

  useEffect(() => {
    onRegisterNewAction(null);
  }, [onRegisterNewAction]);

  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold">Sync history</h2>
      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Account</TH>
              <TH>Status</TH>
              <TH>Fetched</TH>
              <TH>New</TH>
              <TH>Auto-matched</TH>
              <TH>Triggered by</TH>
              <TH>When</TH>
            </tr>
          </THead>
          <tbody>
            {items.map((log) => (
              <TR key={log.id}>
                <TD>{log.account_name ?? "—"}</TD>
                <TD>
                  <StatusBadge status={log.status === "success" ? "active" : "failed"} />
                  {log.status === "failed" && log.status_message && (
                    <span className="ml-2 text-xs text-[var(--text-muted)]">{log.status_message}</span>
                  )}
                </TD>
                <TD>{log.transactions_fetched}</TD>
                <TD>{log.transactions_new}</TD>
                <TD>{log.transactions_matched}</TD>
                <TD>{log.triggered_by_name ?? "Scheduled sync"}</TD>
                <TD>{new Date(log.created_at).toLocaleString()}</TD>
              </TR>
            ))}
            {items.length === 0 && <TR><TD className="text-[var(--text-muted)]">No syncs logged yet.</TD></TR>}
          </tbody>
        </Table>
      )}
    </div>
  );
}
