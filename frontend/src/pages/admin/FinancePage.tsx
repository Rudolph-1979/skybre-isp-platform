import { useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Pager } from "../../components/Pager";
import { SearchableSelect } from "../../components/SearchableSelect";
import { Table, THead, TH, SortableTH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, filterSelectClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { ColumnToggle, type ColumnDef } from "../../components/ColumnToggle";
import { useColumnVisibility } from "../../hooks/useColumnVisibility";
import { apiErrorMessage } from "../../utils/apiError";
import type {
  Invoice, Customer, Product, Tariff, InvoiceItemType, Payment, CreditRequest, InvoiceDeletionRequest,
  Partner, RecurringBillingCounts, RecurringBillingRun, SuspensionSettingsConfig,
} from "../../types";

// Bank Feeds moved to the Accountant page (2026-08-19) -- see
// AccountantPage.tsx's BankFeedsTab and friends. Confirming a bank
// transaction there now creates either a Payment (credit) or an Expense
// (debit), both of which feed the VAT Returns report, so it made more
// sense to live alongside VAT Returns/Expenses than here.
type Tab = "invoices" | "payments" | "credits" | "recurring-billing";

// Unlike the single-button NewAction used elsewhere (Networking, Staff),
// Invoices needs to register two buttons at once ("+ New quote" and
// "+ New invoice"), so this is an array of buttons -- PageHeader's
// `actions` prop happily accepts any ReactNode, including several buttons.
type ActionButton = { label: string; onClick: () => void; variant?: "primary" | "secondary" };
type NewAction = ActionButton[] | null;

const PAGE_SIZE = 50;

export function FinancePage() {
  const { user } = useAuth();
  // Seeded from the URL so a dashboard tile lands on the right tab with the
  // right filter already applied, instead of dropping you on Invoices/All and
  // leaving you to redo the click you just made.
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState<Tab>(() => {
    const wanted = searchParams.get("tab");
    return (["invoices", "payments", "credits", "recurring-billing"] as Tab[]).includes(wanted as Tab)
      ? (wanted as Tab)
      : "invoices";
  });
  const [newAction, setNewAction] = useState<NewAction>(null);

  const canSeeCredits = user?.role === "admin" || user?.role === "accounts" || user?.role === "management";

  const TABS: { key: Tab; label: string }[] = [
    { key: "invoices", label: "Invoices" },
    { key: "payments", label: "Payments" },
    ...(canSeeCredits ? [{ key: "credits" as Tab, label: "Credits" }] : []),
    { key: "recurring-billing", label: "Recurring Billing" },
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

// Which kind of document the list is showing. Separate from the overdue/date
// tabs below, and from the status dropdown, because they answer different
// questions: "show me quotes" vs "show me things overdue by up to 60 days" vs
// "show me exactly the cancelled ones". All three compose.
type DocTypeMode = "" | "quote" | "proforma" | "invoice";

const DOC_TYPE_TABS: { mode: DocTypeMode; label: string }[] = [
  { mode: "", label: "All documents" },
  { mode: "quote", label: "Quotes" },
  { mode: "proforma", label: "Pro formas" },
  { mode: "invoice", label: "Invoices" },
];

const DOC_TYPE_NOUN: Record<DocTypeMode, string> = {
  "": "quotes, pro formas, and invoices",
  quote: "quotes only",
  proforma: "pro formas only",
  invoice: "invoices only — quotes and pro formas excluded",
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
  const [searchParams] = useSearchParams();
  const [statusFilter, setStatusFilter] = useState(() => searchParams.get("status") ?? "");
  const [docType, setDocType] = useState<DocTypeMode>(() => {
    const wanted = searchParams.get("doc");
    return (["quote", "proforma", "invoice"] as DocTypeMode[]).includes(wanted as DocTypeMode)
      ? (wanted as DocTypeMode)
      : "";
  });
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("invoices", ["number"]);

  // Quotes and pro formas can never be "overdue" -- nothing is owed on them
  // yet -- so the overdue presets are hidden while one of those is selected.
  const preInvoiceView = docType === "quote" || docType === "proforma";

  let dateParams = "";
  if (dateFilterMode === "30" || dateFilterMode === "60" || dateFilterMode === "90") {
    dateParams = `&overdue_within_days=${dateFilterMode}`;
  } else if (dateFilterMode === "custom") {
    if (customFrom) dateParams += `&date_created_from=${customFrom}`;
    if (customTo) dateParams += `&date_created_to=${customTo}`;
  }

  // Paginated. This was a flat page_size=100 with no pager anywhere,
  // beside a header printing the true `count` -- so filtering to "0-90
  // days overdue" showed "340 documents" above a table of 100 rows and
  // the other 240 overdue invoices were unreachable from the UI.
  // Collections was working a truncated list with nothing to say so.
  const [page, setPage] = useState(1);
  const listQuery = `${dateParams}${statusFilter ? `&status=${statusFilter}` : ""}${
    docType ? `&document_type=${docType}` : ""
  }`;
  // Any filter or sort change goes back to page one; staying on page 7 of
  // a result set that now has two pages shows an empty table.
  useEffect(() => {
    setPage(1);
  }, [listQuery, ordering]);
  const { items, count, loading, refetch } = useApiList<Invoice>(
    `/invoices/?page_size=${PAGE_SIZE}&page=${page}&ordering=${ordering}${listQuery}`
  );
  const [createError, setCreateError] = useState("");
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
    api.get<{ results: Customer[] }>("/customers/picker/").then((res) => setCustomers(res.data.results));
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
    // Fills the price from the product's resell price, the same way
    // selectTariff fills it from the tariff. It used to fill only the
    // description, so every stock line's price had to be typed from memory --
    // which is how the same item ends up quoted at three different prices.
    //
    // A product with no resell price set leaves unit_price blank rather than
    // putting a 0 in: 0 is a real price (a giveaway), and quietly inventing
    // one is worse than an empty field the person has to look at.
    updateItem(idx, {
      product: productId,
      description: p ? p.name : "",
      unit_price: p?.sell_price ?? "",
      tax_rate_pct: p?.sell_tax_rate_pct ?? "15",
    });
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
    setCreateError("");
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
    } catch (err) {
      // Reachable without trying anything unusual: the Qty field is
      // neither required nor min-constrained, so clearing it 400s on
      // items[].quantity -- and clicking "Create invoice" then did
      // nothing at all, forever, with no message and the modal still open.
      setCreateError(apiErrorMessage(err, "Could not create this document."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      {canDecideDeletion && <PendingInvoiceDeletionRequestsPanel />}

      <p className="mb-3 text-sm text-[var(--text-secondary)]">
        {count} document{count === 1 ? "" : "s"} matching current filter ({DOC_TYPE_NOUN[docType]})
      </p>

      {/* Document type first, on its own row: it's the question people ask
          most ("show me the quotes"), and it was previously buried in a
          status dropdown below the overdue buttons. */}
      <div className="mb-2 flex flex-wrap items-center gap-2">
        {DOC_TYPE_TABS.map((tab) => (
          <button
            key={tab.mode || "all"}
            type="button"
            onClick={() => {
              setDocType(tab.mode);
              // An overdue preset would guarantee an empty list for a quote
              // or pro forma, so drop back to All when switching to one.
              if ((tab.mode === "quote" || tab.mode === "proforma") &&
                  ["30", "60", "90"].includes(dateFilterMode)) {
                setDateFilterMode("all");
              }
              // An exact-status filter left over from a previous look would
              // fight this one -- status=paid with Quotes selected returns
              // nothing, and it isn't obvious why. Clearing it keeps the
              // buttons honest; the dropdown is still there to narrow again.
              setStatusFilter("");
            }}
            className={
              docType === tab.mode
                ? "rounded-md bg-[var(--series-1)] px-3 py-1.5 text-sm font-medium text-white"
                : "rounded-md border border-[var(--baseline)] px-3 py-1.5 text-sm font-medium text-[var(--text-primary)] hover:bg-[var(--tint-hover)]"
            }
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {DATE_FILTER_TABS.filter(
          // The overdue presets match only unpaid/overdue documents, so they
          // can never return a quote or a pro forma -- offering them here
          // would just be a way to get an empty list and wonder why. The
          // custom date range works on date_created, so it stays.
          (tab) => !(preInvoiceView && ["30", "60", "90"].includes(tab.mode))
        ).map((tab) => (
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
        <>
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
            {/* An empty list should say WHY it's empty. "No documents" while
                the Quotes button is lit had people wondering whether the
                listing was broken rather than whether any quotes existed. */}
            {items.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]">
                  {docType === "quote"
                    ? "No quotes on the system yet — create one with “+ New quote” above."
                    : docType === "proforma"
                      ? "No pro formas. One is created by converting a quote, on the quote's own page."
                      : "No documents match the current filters."}
                </TD>
              </TR>
            )}
          </tbody>
        </Table>
        <Pager page={page} pageSize={PAGE_SIZE} count={count} shown={items.length} onPageChange={setPage} label="documents" />
        </>
      )}

      {showModal && (
        <Modal
          title={modalKind === "quote" ? "New quote" : "New invoice"}
          onClose={() => { setShowModal(false); setCreateError(""); }}
        >
          <form onSubmit={handleSubmit}>
            {createError && (
              <p className="mb-3 rounded-md border border-[var(--status-critical)] bg-[var(--tint-subtle)] p-2 text-sm text-[var(--status-critical)]">
                {createError}
              </p>
            )}
            <FormField label="Customer">
<SearchableSelect
                options={customers.map((c) => ({
                  value: String(c.id),
                  label: c.full_name,
                  meta: c.customer_id,
                  searchText: `${c.full_name} ${c.company_name ?? ""} ${c.customer_id}`,
                }))}
                value={customer}
                onChange={(v) => setCustomer(v)}
                placeholder="Select customer…"
                hint="Search by name or payment reference."
                required
              />
            </FormField>
            <FormField label={modalKind === "quote" ? "Valid until" : "Due date"}>
              <input type="date" className={inputClass} required value={dateDue} onChange={(e) => setDateDue(e.target.value)} />
            </FormField>

            <p className="mb-1 text-sm font-medium text-[var(--text-secondary)]">Line items</p>
            {/* Each line picks its own type, so one document can carry a
                recurring plan and the hardware it needs together -- which is
                what a real installation quote looks like. */}
            <p className="mb-2 text-xs text-[var(--text-muted)]">
              Mix freely: a <strong>Tariff plan</strong> line for the recurring subscription, <strong>Stock item</strong>
              {" "}lines for the hardware, <strong>Custom</strong> for anything else — all on the same document. Stock
              items price themselves from their resell price (Inventory → Products).
            </p>
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
                        <option key={p.id} value={p.id}>
                          {p.name}
                          {p.sku ? ` (${p.sku})` : ""}
                          {p.sell_price ? ` — R ${parseFloat(p.sell_price).toFixed(2)}` : " — no resell price set"}
                        </option>
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
    api.get<{ results: Customer[] }>("/customers/picker/").then((res) => setCustomers(res.data.results));
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
    } catch (err) {
      setError(apiErrorMessage(err, "Could not reject this credit request."));
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

