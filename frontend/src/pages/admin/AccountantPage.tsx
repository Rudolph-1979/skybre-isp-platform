import { useEffect, useMemo, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Pager } from "../../components/Pager";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { SearchableSelect, type SearchableOption } from "../../components/SearchableSelect";
import { PdfPreviewModal } from "../../components/PdfPreviewModal";
import { Modal, FormField, inputClass, filterSelectClass, btnPrimary, btnSecondary } from "../../components/Modal";
import {
  EXPENSE_CATEGORY_LABELS,
  type AttendanceRecord,
  type BankAccount,
  type BankTransaction,
  type BankTransactionStatus,
  type BankFeedSyncLog,
  type BankStatementImportPreview,
  type BankStatementImportResult,
  type Customer,
  type Expense,
  type ExpenseCategory,
  type LeaveRequest,
  type LeaveType,
  type PayrollRun,
  type PayrollRunLine,
  type PayrollSettingsConfig,
  type PayType,
  type Supplier,
  type StaffProfile,
  type User,
  type VatReturnResult,
} from "../../types";
import { apiErrorMessage } from "../../utils/apiError";

// Everything here lives under one "Accountant" nav item -- VAT Returns,
// Expenses, and Bank Feeds are the reason this page exists; Attendance/
// Leave/Employees/Payroll were folded in from the old standalone Staff
// page (2026-08-19) since payroll is itself an accounting concern, and it
// kept the sidebar from having two separate "admin/HR-ish" top-level
// items. Bank Feeds itself moved here from Finance (2026-08-19) once
// confirming a transaction started being able to create an Expense (a
// debit) as well as a Payment (a credit) -- both outcomes are Accountant
// concerns first.
type Tab = "vat-returns" | "expenses" | "bank-feeds" | "attendance" | "leave" | "employees" | "payroll";
type NewAction = { label: string; onClick: () => void } | null;

const PAY_TYPE_LABEL: Record<PayType, string> = {
  salary: "Monthly salary",
  hourly: "Hourly rate",
};

const LEAVE_TYPE_LABEL: Record<LeaveType, string> = {
  annual: "Annual leave",
  sick: "Sick leave",
  family_responsibility: "Family responsibility leave",
};

const LEAVE_BALANCE_FIELD: Record<LeaveType, "annual_leave_balance" | "sick_leave_balance" | "family_responsibility_leave_balance"> = {
  annual: "annual_leave_balance",
  sick: "sick_leave_balance",
  family_responsibility: "family_responsibility_leave_balance",
};


function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString("en-ZA", { hour: "2-digit", minute: "2-digit" });
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString();
}

export function AccountantPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [tab, setTab] = useState<Tab>("vat-returns");
  const [newAction, setNewAction] = useState<NewAction>(null);
  const [attendanceVersion, setAttendanceVersion] = useState(0);

  const TABS: { key: Tab; label: string }[] = [
    { key: "vat-returns", label: "VAT Returns" },
    { key: "expenses", label: "Expenses" },
    { key: "bank-feeds", label: "Bank Feeds" },
    { key: "attendance", label: "Attendance Register" },
    { key: "leave", label: "Leave" },
    ...(isAdmin
      ? ([
          { key: "employees", label: "Employees" },
          { key: "payroll", label: "Payroll" },
        ] as { key: Tab; label: string }[])
      : []),
  ];

  return (
    <div>
      <PageHeader
        title="Accountant"
        subtitle="VAT returns, business expenses, and staff attendance/payroll."
        actions={
          <>
            {(tab === "attendance" || tab === "leave" || tab === "employees" || tab === "payroll") && (
              <ClockInOutWidget onChange={() => setAttendanceVersion((v) => v + 1)} />
            )}
            {newAction && (
              <button className={btnPrimary} onClick={newAction.onClick}>
                {newAction.label}
              </button>
            )}
          </>
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
      {tab === "vat-returns" && <VatReturnsTab onRegisterNewAction={setNewAction} />}
      {tab === "expenses" && <ExpensesTab onRegisterNewAction={setNewAction} />}
      {tab === "bank-feeds" && <BankFeedsTab onRegisterNewAction={setNewAction} isAdmin={isAdmin} />}
      {tab === "attendance" && (
        <AttendanceTab isAdmin={isAdmin} onRegisterNewAction={setNewAction} refreshSignal={attendanceVersion} />
      )}
      {tab === "leave" && <LeaveTab isAdmin={isAdmin} onRegisterNewAction={setNewAction} />}
      {tab === "employees" && isAdmin && <EmployeesTab onRegisterNewAction={setNewAction} />}
      {tab === "payroll" && isAdmin && <PayrollTab onRegisterNewAction={setNewAction} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// VAT Returns -- two-monthly (SARS Category B: periods ending Feb / Apr /
// Jun / Aug / Oct / Dec) Output VAT (from real invoices) / Input VAT (from
// Expense records below) / Net VAT calculation. See backend
// expenses.views.build_vat_return for exactly what's summed and why.
// ---------------------------------------------------------------------------

const VAT_PERIODS: { label: string; startMonth: number; endMonth: number }[] = [
  { label: "Jan – Feb", startMonth: 0, endMonth: 1 },
  { label: "Mar – Apr", startMonth: 2, endMonth: 3 },
  { label: "May – Jun", startMonth: 4, endMonth: 5 },
  { label: "Jul – Aug", startMonth: 6, endMonth: 7 },
  { label: "Sep – Oct", startMonth: 8, endMonth: 9 },
  { label: "Nov – Dec", startMonth: 10, endMonth: 11 },
];

function pad2(n: number) {
  return String(n).padStart(2, "0");
}

// Deliberately builds the YYYY-MM-DD string from plain y/m/d numbers
// instead of Date#toISOString() -- toISOString() converts through UTC,
// which can shift the date by a day near a timezone boundary. This never
// touches UTC at all, so there's no boundary to get wrong.
function periodBounds(year: number, periodIndex: number) {
  const p = VAT_PERIODS[periodIndex];
  const start = `${year}-${pad2(p.startMonth + 1)}-01`;
  const lastDay = new Date(year, p.endMonth + 1, 0).getDate();
  const end = `${year}-${pad2(p.endMonth + 1)}-${pad2(lastDay)}`;
  return { start, end };
}

function currentPeriod() {
  const now = new Date();
  return { year: now.getFullYear(), index: Math.floor(now.getMonth() / 2) };
}

function VatReturnsTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const [{ year, index }, setPeriod] = useState(currentPeriod());
  const [result, setResult] = useState<VatReturnResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    onRegisterNewAction(null);
  }, [onRegisterNewAction]);

  useEffect(() => {
    setLoading(true);
    setError("");
    const { start, end } = periodBounds(year, index);
    api
      .get<VatReturnResult>(`/vat-return/?period_start=${start}&period_end=${end}`)
      .then((r) => setResult(r.data))
      .catch(() => setError("Could not load the VAT return for this period."))
      .finally(() => setLoading(false));
  }, [year, index]);

  function goPrev() {
    setPeriod((p) => (p.index === 0 ? { year: p.year - 1, index: 5 } : { year: p.year, index: p.index - 1 }));
  }

  function goNext() {
    setPeriod((p) => (p.index === 5 ? { year: p.year + 1, index: 0 } : { year: p.year, index: p.index + 1 }));
  }

  async function handleDownloadPdf() {
    setDownloading(true);
    try {
      const { start, end } = periodBounds(year, index);
      const res = await api.get(`/vat-return/pdf/?period_start=${start}&period_end=${end}`, { responseType: "blob" });
      // Keep the application/pdf type, attach the anchor to the document
      // before clicking, and revoke on the next tick. Revoking in the
      // same task as click() on a detached anchor is the fragile form of
      // this pattern -- Firefox needs the node connected to dispatch the
      // click, and Safari can invalidate the URL before the download
      // starts.
      const url = window.URL.createObjectURL(new Blob([res.data], { type: "application/pdf" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `VAT-Return-${start}-to-${end}.pdf`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      setTimeout(() => window.URL.revokeObjectURL(url), 0);
    } catch {
      alert("Could not download the VAT report — please try again.");
    } finally {
      setDownloading(false);
    }
  }

  const { start, end } = periodBounds(year, index);

  return (
    <div>
      <p className="mb-4 max-w-2xl text-sm text-[var(--text-secondary)]">
        Output VAT is calculated from real invoices issued in this period (invoice/accrual basis) — not just
        payments received. Input VAT comes from two places: the <strong>Expenses</strong> tab (rent, bandwidth,
        fuel…) and <strong>stock receipts</strong> in Stock/Inventory (equipment bought for stock) — so an
        equipment invoice should be captured in one place only. Approved credit notes are shown for information
        only; they aren't automatically netted against Output VAT since credit requests don't record a VAT rate of
        their own. Double-check everything here against your own records before submitting to SARS.
      </p>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <button type="button" className={btnSecondary} onClick={goPrev}>‹ Previous period</button>
        <div className="rounded-md border border-[var(--border-hairline)] px-4 py-2 text-sm font-medium text-[var(--text-primary)]">
          {VAT_PERIODS[index].label} {year} <span className="text-[var(--text-muted)]">({start} to {end})</span>
        </div>
        <button type="button" className={btnSecondary} onClick={goNext}>Next period ›</button>
        <button type="button" className={btnPrimary} disabled={downloading || loading || !!error} onClick={handleDownloadPdf}>
          {downloading ? "Downloading…" : "Download PDF"}
        </button>
      </div>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : error ? (
        <p className="text-sm text-[var(--status-critical)]">{error}</p>
      ) : result ? (
        <>
        {/* A supplier invoice captured BOTH as a stock receipt and as an
            expense. Deliberately a warning, not an automatic deduction:
            two different invoices can share a number, and a report that
            quietly adjusted the figures would be worse than one that asks
            someone to look. Over-claimed Input VAT is what SARS penalises. */}
        {result.duplicate_claims.length > 0 && (
          <div className="mb-4 rounded-lg border-2 border-[#b3852e] bg-[var(--surface-1)] p-4">
            <h3 className="text-sm font-semibold text-[#b3852e]">
              {result.duplicate_claims.length} invoice
              {result.duplicate_claims.length === 1 ? "" : "s"} may be claimed twice
            </h3>
            <p className="mt-1 max-w-3xl text-sm text-[var(--text-secondary)]">
              These supplier invoices were captured as a stock receipt <em>and</em> as an expense. The
              Input VAT above counts both, so it is overstated by the amounts shown. Delete whichever
              record is the duplicate — equipment invoices belong in Stock/Inventory only. Nothing has
              been adjusted automatically.
            </p>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wide text-[var(--text-muted)]">
                    <th className="pb-1 pr-4">Supplier</th>
                    <th className="pb-1 pr-4">Invoice #</th>
                    <th className="pb-1 pr-4">Stock receipt</th>
                    <th className="pb-1 pr-4">Expense</th>
                    <th className="pb-1 text-right">Overstated by</th>
                  </tr>
                </thead>
                <tbody>
                  {result.duplicate_claims.map((d) => (
                    <tr key={`${d.receipt_id}-${d.expense_id}`} className="border-t border-[var(--border-hairline)]">
                      <td className="py-1.5 pr-4">{d.supplier}</td>
                      <td className="py-1.5 pr-4 font-medium">{d.invoice_number}</td>
                      <td className="py-1.5 pr-4 text-[var(--text-secondary)]">
                        {d.receipt_date} · R {d.receipt_vat.toFixed(2)} VAT
                      </td>
                      <td className="py-1.5 pr-4 text-[var(--text-secondary)]">
                        {d.expense_date} · {d.expense_description}
                      </td>
                      <td className="py-1.5 text-right tabular-nums font-medium">
                        R {Math.min(d.receipt_vat, d.expense_vat).toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4">
            <h3 className="mb-3 text-sm font-semibold text-[var(--text-primary)]">Output VAT (on sales)</h3>
            <dl className="space-y-1 text-sm">
              <div className="flex justify-between"><dt className="text-[var(--text-secondary)]">Standard-rated supplies (excl. VAT)</dt><dd className="tabular-nums">R {result.output.standard_rated_supplies.toFixed(2)}</dd></div>
              <div className="flex justify-between"><dt className="text-[var(--text-secondary)]">Zero-rated supplies (excl. VAT)</dt><dd className="tabular-nums">R {result.output.zero_rated_supplies.toFixed(2)}</dd></div>
              <div className="flex justify-between"><dt className="text-[var(--text-secondary)]">Invoices included</dt><dd className="tabular-nums">{result.output.invoice_count}</dd></div>
              <div className="mt-2 flex justify-between border-t border-[var(--border-hairline)] pt-2 font-semibold"><dt>Output VAT</dt><dd className="tabular-nums">R {result.output.output_vat.toFixed(2)}</dd></div>
            </dl>
          </div>

          <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4">
            <h3 className="mb-3 text-sm font-semibold text-[var(--text-primary)]">Input VAT (on purchases/expenses)</h3>
            <dl className="space-y-1 text-sm">
              <div className="flex justify-between"><dt className="text-[var(--text-secondary)]">Purchases (excl. VAT)</dt><dd className="tabular-nums">R {result.input.purchases_excl_vat.toFixed(2)}</dd></div>
              <div className="mt-2 flex justify-between border-t border-[var(--border-hairline)] pt-2 font-semibold"><dt>Input VAT</dt><dd className="tabular-nums">R {result.input.input_vat.toFixed(2)}</dd></div>
            </dl>

            {/* Split by source so each side can be reconciled against its
                own tab. Equipment used to be missing from this figure
                entirely, which under-claimed Input VAT on every router and
                drum of cable bought. */}
            <div className="mt-3 border-t border-[var(--border-hairline)] pt-3">
              <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">
                Where it comes from
              </p>
              <dl className="space-y-1 text-sm">
                <div className="flex justify-between">
                  <dt className="text-[var(--text-secondary)]">
                    Expenses <span className="text-[var(--text-muted)]">({result.input.expenses.count})</span>
                  </dt>
                  <dd className="tabular-nums">R {result.input.expenses.input_vat.toFixed(2)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-[var(--text-secondary)]">
                    Stock receipts <span className="text-[var(--text-muted)]">({result.input.stock.count})</span>
                  </dt>
                  <dd className="tabular-nums">R {result.input.stock.input_vat.toFixed(2)}</dd>
                </div>
              </dl>
              {result.input.stock.receipts_missing_vat > 0 && (
                <p className="mt-2 text-xs text-[#b3852e]">
                  {result.input.stock.receipts_missing_vat} stock receipt
                  {result.input.stock.receipts_missing_vat === 1 ? " has" : "s have"} lines captured before VAT
                  tracking existed. Those claim no VAT, so this figure may be understated.
                </p>
              )}
            </div>
          </div>

          <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4">
            <h3 className="mb-3 text-sm font-semibold text-[var(--text-primary)]">Credit notes issued (informational only)</h3>
            <dl className="space-y-1 text-sm">
              <div className="flex justify-between"><dt className="text-[var(--text-secondary)]">Approved this period</dt><dd className="tabular-nums">R {result.credit_notes.total_amount.toFixed(2)}</dd></div>
              <div className="flex justify-between"><dt className="text-[var(--text-secondary)]">Count</dt><dd className="tabular-nums">{result.credit_notes.count}</dd></div>
            </dl>
          </div>

          <div className="rounded-lg border-2 border-[var(--series-1)] bg-[var(--surface-1)] p-4">
            <h3 className="mb-3 text-sm font-semibold text-[var(--text-primary)]">
              Net VAT {result.net_vat_direction === "payable" ? "payable to SARS" : result.net_vat_direction === "refundable" ? "refundable from SARS" : ""}
            </h3>
            <div className="text-2xl font-bold tabular-nums text-[var(--text-primary)]">R {result.net_vat.toFixed(2)}</div>
            <p className="mt-2 text-xs text-[var(--text-muted)]">
              {result.vat_number ? `VAT reg. ${result.vat_number}` : "No VAT registration number set — add it under Configs → Billing."}
              {" · "}Category {result.vat_category} · {result.basis} basis
            </p>
          </div>
        </div>
        </>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Expenses -- the Input VAT side of the VAT Returns report above. See
// backend expenses.models.Expense for why this exists (there was no
// expense/purchase tracking anywhere in this codebase before it).
// ---------------------------------------------------------------------------

const EMPTY_EXPENSE_FORM = {
  supplier: "",
  supplier_name: "",
  category: "other" as ExpenseCategory,
  description: "",
  invoice_number: "",
  date: "",
  amount_excl_vat: "",
  vat_rate_pct: "15",
  notes: "",
};

function ExpensesTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const { items, loading, refetch } = useApiList<Expense>("/expenses/?page_size=200&ordering=-date");
  const { items: suppliers } = useApiList<Supplier>("/suppliers/?page_size=200&ordering=name");
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<Expense | null>(null);
  const [form, setForm] = useState(EMPTY_EXPENSE_FORM);
  const [attachment, setAttachment] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [deleteError, setDeleteError] = useState("");

  useEffect(() => {
    onRegisterNewAction({
      label: "+ Add expense",
      onClick: () => {
        setEditing(null);
        setForm(EMPTY_EXPENSE_FORM);
        setAttachment(null);
        setError("");
        setShowModal(true);
      },
    });
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function openEdit(expense: Expense) {
    setEditing(expense);
    setForm({
      supplier: expense.supplier ? String(expense.supplier) : "",
      supplier_name: expense.supplier_name,
      category: expense.category,
      description: expense.description,
      invoice_number: expense.invoice_number,
      date: expense.date,
      amount_excl_vat: expense.amount_excl_vat,
      vat_rate_pct: expense.vat_rate_pct,
      notes: expense.notes,
    });
    setAttachment(null);
    setError("");
    setShowModal(true);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      const fd = new FormData();
      // Always send `supplier`, including as an empty string. Omitting it
      // when blank meant an existing supplier link could never be
      // cleared: the FK silently survived while a contradictory
      // free-text supplier_name was saved next to it, and the table kept
      // showing the old supplier. The serializer maps "" to null.
      fd.append("supplier", form.supplier);
      fd.append("supplier_name", form.supplier_name);
      fd.append("category", form.category);
      fd.append("description", form.description);
      fd.append("invoice_number", form.invoice_number);
      fd.append("date", form.date);
      fd.append("amount_excl_vat", form.amount_excl_vat);
      fd.append("vat_rate_pct", form.vat_rate_pct);
      fd.append("notes", form.notes);
      if (attachment) fd.append("attachment", attachment);
      if (editing) {
        await api.patch(`/expenses/${editing.id}/`, fd, { headers: { "Content-Type": "multipart/form-data" } });
      } else {
        await api.post("/expenses/", fd, { headers: { "Content-Type": "multipart/form-data" } });
      }
      setShowModal(false);
      refetch();
    } catch (err) {
      setError(apiErrorMessage(err, "Could not save this expense — please try again."));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(expense: Expense) {
    const fromFeed = expense.from_bank_feed
      ? " It came from a bank transaction, which will go back to the Bank Feeds review queue so the money isn't lost."
      : "";
    if (!confirm(`Delete this expense (${expense.description}, R${expense.amount_excl_vat})? This can't be undone.${fromFeed}`)) return;
    try {
      await api.delete(`/expenses/${expense.id}/`);
      refetch();
    } catch (err) {
      // Previously unhandled: a failed delete left the row on screen with
      // no message at all, so it looked like the click hadn't registered.
      setDeleteError(apiErrorMessage(err, "Could not delete this expense — please try again."));
    }
  }

  return (
    <div>
      <p className="mb-3 text-sm text-[var(--text-secondary)]">
        Business expenses/purchases — rent, bandwidth, equipment, fuel, and so on. The date should be the one on the
        supplier's own invoice/receipt, since that's what determines which VAT period this expense's Input VAT
        falls into on the VAT Returns tab.
      </p>

      {deleteError && (
        <p className="mb-3 rounded-md border border-[var(--status-critical)] px-3 py-2 text-sm text-[var(--status-critical)]">
          {deleteError}
        </p>
      )}

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Date</TH>
              <TH>Supplier</TH>
              <TH>Category</TH>
              <TH>Description</TH>
              <TH>Amount (excl. VAT)</TH>
              <TH>VAT rate</TH>
              <TH>VAT amount</TH>
              <TH>Total (incl. VAT)</TH>
              <TH>Receipt</TH>
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((expense) => (
              <TR key={expense.id}>
                <TD>{formatDate(expense.date)}</TD>
                <TD className="font-medium">{expense.supplier_display_name}</TD>
                <TD>{EXPENSE_CATEGORY_LABELS[expense.category]}</TD>
                <TD>
                  {expense.description}
                  {expense.from_bank_feed && (
                    <span className="ml-2 text-xs text-[var(--text-muted)]" title="Created from a confirmed Bank Feeds transaction">
                      via bank feed
                    </span>
                  )}
                </TD>
                <TD className="tabular-nums">R {expense.amount_excl_vat}</TD>
                <TD className="tabular-nums">{expense.vat_rate_pct}%</TD>
                <TD className="tabular-nums">R {expense.vat_amount}</TD>
                <TD className="tabular-nums font-medium">R {expense.amount_incl_vat}</TD>
                <TD>
                  {expense.attachment ? (
                    <a href={expense.attachment} target="_blank" rel="noreferrer" className="text-[var(--series-1)] hover:underline">
                      View
                    </a>
                  ) : (
                    "—"
                  )}
                </TD>
                <TD>
                  <button className="text-xs text-[var(--series-1)] hover:underline" onClick={() => openEdit(expense)}>
                    Edit
                  </button>
                  <button
                    className="ml-2 text-xs text-[var(--status-critical)] hover:underline"
                    onClick={() => handleDelete(expense)}
                  >
                    Delete
                  </button>
                </TD>
              </TR>
            ))}
            {items.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]">No expenses logged yet.</TD>
              </TR>
            )}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title={editing ? "Edit expense" : "Add expense"} onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit} autoComplete="off">
            <FormField label="Supplier (optional — pick an existing one)">
              <SearchableSelect
                options={suppliers.map((sup) => ({ value: String(sup.id), label: sup.name }))}
                value={form.supplier}
                onChange={(v) => setForm({ ...form, supplier: v })}
                placeholder="Not linked to a Supplier record"
                emptyLabel="Not linked to a Supplier record"
              />
            </FormField>
            {!form.supplier && (
              <FormField label="Supplier / payee name">
                <input
                  className={inputClass}
                  value={form.supplier_name}
                  onChange={(e) => setForm({ ...form, supplier_name: e.target.value })}
                  placeholder="e.g. City of Johannesburg"
                />
              </FormField>
            )}
            <FormField label="Category">
              <select
                className={inputClass}
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value as ExpenseCategory })}
              >
                {Object.entries(EXPENSE_CATEGORY_LABELS).map(([v, l]) => (
                  <option key={v} value={v}>{l}</option>
                ))}
              </select>
            </FormField>
            <FormField label="Description">
              <input
                className={inputClass}
                required
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
            </FormField>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <FormField label="Invoice/receipt number (optional)">
                <input
                  className={inputClass}
                  value={form.invoice_number}
                  onChange={(e) => setForm({ ...form, invoice_number: e.target.value })}
                />
              </FormField>
              <FormField label="Invoice/receipt date">
                <input
                  type="date"
                  className={inputClass}
                  required
                  value={form.date}
                  onChange={(e) => setForm({ ...form, date: e.target.value })}
                />
              </FormField>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <FormField label="Amount (excl. VAT)">
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  className={inputClass}
                  required
                  value={form.amount_excl_vat}
                  onChange={(e) => setForm({ ...form, amount_excl_vat: e.target.value })}
                />
              </FormField>
              <FormField label="VAT rate % (0 for zero-rated/exempt)">
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  className={inputClass}
                  required
                  value={form.vat_rate_pct}
                  onChange={(e) => setForm({ ...form, vat_rate_pct: e.target.value })}
                />
              </FormField>
            </div>
            <FormField label="Receipt / supplier invoice (optional)">
              <input
                type="file"
                accept=".pdf,.png,.jpg,.jpeg,.webp,.heic,.gif,.txt"
                className={inputClass}
                onChange={(e) => setAttachment(e.target.files?.[0] ?? null)}
              />
            </FormField>
            <FormField label="Notes (optional)">
              <input
                className={inputClass}
                value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
              />
            </FormField>
            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" className={btnPrimary} disabled={saving}>
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bank Feeds -- FNB accounts (Admin-only), the transaction review queue
// (assign/confirm/ignore/unmatch, CSV import), and sync History. Moved
// here from Finance (2026-08-19) -- confirming a credit still creates a
// Payment same as before, but confirming a debit now creates an Expense
// (Input VAT), so both outcomes land in the Accountant section they
// actually belong to. Direct API access to FNB isn't confirmed yet (see
// the backend's fnb_client.py docstring) -- CSV import is the practical
// way to use this today; the same review queue and every action works
// identically regardless of which source a transaction came from.
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
  const [syncError, setSyncError] = useState("");

  useEffect(() => {
    onRegisterNewAction({
      label: "+ New account",
      onClick: () => {
        setEditing(null);
        setForm(EMPTY_BANK_ACCOUNT_FORM);
        setError("");
        setShowModal(true);
      },
    });
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
      setError(apiErrorMessage(err, "Could not save this account — please try again."));
    } finally {
      setSaving(false);
    }
  }

  async function handleSyncNow(id: number) {
    setSyncingId(id);
    try {
      await api.post(`/bank-accounts/${id}/sync-now/`);
      refetch();
    } catch (err) {
      // The FNB client's errors are the whole point of this button --
      // they now name the status, the URL, the redirect chain and the
      // bank's own response body. Swallowing them made it useless.
      setSyncError(apiErrorMessage(err, "Could not sync this account."));
    } finally {
      setSyncingId(null);
    }
  }

  return (
    <div>
      {syncError && (
        <p className="mb-3 rounded-md border border-[var(--status-critical)] bg-[var(--tint-subtle)] p-2 text-sm text-[var(--status-critical)]">
          {syncError}
        </p>
      )}
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

const REVIEW_PAGE_SIZE = 50;

function BankFeedsReviewSubTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const [statusFilter, setStatusFilter] = useState<BankTransactionStatus | "">("unmatched");
  // Paginated. This was a flat page_size=100 with no pager: one imported
  // FNB statement for 1,592 EFT payers routinely leaves more than 100
  // credits unmatched, and everything past the newest 100 could never be
  // allocated to a customer -- so that money sat unapplied while those
  // customers stayed overdue, with nothing on screen saying the queue was
  // longer than the page.
  const [page, setPage] = useState(1);
  useEffect(() => {
    setPage(1);
  }, [statusFilter]);
  const { items, count, loading, refetch } = useApiList<BankTransaction>(
    `/bank-transactions/?page_size=${REVIEW_PAGE_SIZE}&page=${page}&ordering=-date${
      statusFilter ? `&status=${statusFilter}` : ""
    }`
  );
  const [accounts, setAccounts] = useState<BankAccount[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [selectedCustomer, setSelectedCustomer] = useState<Record<number, string>>({});
  const [selectedSupplier, setSelectedSupplier] = useState<Record<number, string>>({});
  const [busyId, setBusyId] = useState<number | null>(null);
  const [rowError, setRowError] = useState<Record<number, string>>({});
  const [loadError, setLoadError] = useState("");
  const [customersTruncated, setCustomersTruncated] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [expenseModalTxn, setExpenseModalTxn] = useState<BankTransaction | null>(null);

  // Each of these backs a dropdown on the review rows. They used to be
  // bare .then() chains, so a 403 (or any failure) left the dropdown
  // silently empty with no explanation and an unhandled rejection in the
  // console -- the user just saw a picker with nothing in it. Now a
  // failure says so.
  // Mapped once per list rather than per row: the review table renders a
  // picker on every unmatched transaction, and rebuilding the options inside
  // each of them would redo the work for every row on every keystroke.
  //
  // The reference goes in `searchText` as well as `meta` so typing "SWF2795"
  // finds the customer even though it isn't part of their name -- which is
  // exactly what you have in front of you when reading a bank description.
  const customerOptions = useMemo<SearchableOption[]>(
    () =>
      customers.map((c) => ({
        value: String(c.id),
        label: c.full_name,
        meta: c.customer_id,
        searchText: `${c.full_name} ${c.company_name ?? ""} ${c.customer_id} ${c.email ?? ""}`,
      })),
    [customers]
  );

  const supplierOptions = useMemo<SearchableOption[]>(
    () => suppliers.map((s) => ({ value: String(s.id), label: s.name })),
    [suppliers]
  );

  useEffect(() => {
    api.get<{ results: BankAccount[] }>("/bank-accounts/?page_size=50&ordering=name")
      .then((res) => setAccounts(res.data.results))
      .catch(() => setLoadError("Could not load the bank account list — importing a statement won't work until that's fixed."));
    api.get<{ results: Customer[]; count: number }>("/customers/picker/")
      .then((res) => {
        setCustomers(res.data.results);
        // The picker searches the list it was given, so a truncated list would
        // quietly fail to find someone who does exist. Better to say so than
        // to let an allocation be abandoned because a name "isn't there".
        setCustomersTruncated(res.data.count > res.data.results.length);
      })
      .catch(() => setLoadError("Could not load the customer list — you won't be able to allocate money-in transactions."));
    api.get<{ results: Supplier[] }>("/suppliers/?page_size=500&ordering=name")
      .then((res) => setSuppliers(res.data.results))
      .catch(() => setLoadError("Could not load the supplier list — you can still confirm an expense by typing a supplier name."));
  }, []);

  useEffect(() => {
    onRegisterNewAction({ label: "Import statement CSV", onClick: () => setShowImport(true) });
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleAssignCustomer(txn: BankTransaction) {
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

  async function handleAssignSupplier(txn: BankTransaction) {
    const supplierId = selectedSupplier[txn.id];
    if (!supplierId) return;
    setBusyId(txn.id);
    setRowError((prev) => ({ ...prev, [txn.id]: "" }));
    try {
      await api.post(`/bank-transactions/${txn.id}/assign/`, { supplier: supplierId });
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setRowError((prev) => ({ ...prev, [txn.id]: detail || "Could not assign this supplier." }));
    } finally {
      setBusyId(null);
    }
  }

  async function handleConfirmCredit(txn: BankTransaction) {
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

  // Both of these can legitimately fail -- e.g. another staff member
  // confirmed the transaction a moment ago, which the backend rejects
  // with a 400 explaining why. They used to swallow that silently, so
  // the row simply didn't change and the user had no idea why.
  async function handleIgnore(txn: BankTransaction) {
    setBusyId(txn.id);
    setRowError((prev) => ({ ...prev, [txn.id]: "" }));
    try {
      await api.post(`/bank-transactions/${txn.id}/ignore/`);
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setRowError((prev) => ({ ...prev, [txn.id]: detail || "Could not ignore this transaction." }));
    } finally {
      setBusyId(null);
    }
  }

  async function handleUnmatch(txn: BankTransaction) {
    setBusyId(txn.id);
    setRowError((prev) => ({ ...prev, [txn.id]: "" }));
    try {
      await api.post(`/bank-transactions/${txn.id}/unmatch/`);
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setRowError((prev) => ({ ...prev, [txn.id]: detail || "Could not unmatch this transaction." }));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div>
      <p className="mb-3 text-sm text-[var(--text-secondary)]">
        Bank transactions matched by reference where possible — a credit (money in) to a customer by the reference
        number in the description, a debit (money out) to a supplier by name. Nothing becomes a real Payment or
        Expense (or changes any balance) until you click Confirm.
      </p>

      {loadError && (
        <p className="mb-3 rounded-md border border-[var(--status-critical)] px-3 py-2 text-sm text-[var(--status-critical)]">
          {loadError}
        </p>
      )}

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
        <>
        <Table>
          <THead>
            <tr>
              <TH>Date</TH>
              <TH>Account</TH>
              <TH>Description</TH>
              <TH>Amount</TH>
              <TH>Customer / Supplier</TH>
              <TH>Status</TH>
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((txn) => {
              const isCredit = parseFloat(txn.amount) > 0;
              return (
                <TR key={txn.id}>
                  <TD>{txn.date}</TD>
                  <TD className="text-[var(--text-secondary)]">{txn.account_name}</TD>
                  <TD className="max-w-xs truncate"><span title={txn.description}>{txn.description}</span></TD>
                  <TD className={`tabular-nums ${!isCredit ? "text-[var(--text-muted)]" : ""}`}>
                    R {parseFloat(txn.amount).toFixed(2)}
                  </TD>
                  <TD>
                    {txn.status === "confirmed" || txn.status === "ignored" ? (
                      (isCredit ? txn.matched_customer_name : txn.matched_supplier_name) || <span className="text-[var(--text-muted)]">—</span>
                    ) : isCredit ? (
                      <SearchableSelect
                        className="min-w-56"
                        options={customerOptions}
                        value={selectedCustomer[txn.id] ?? (txn.matched_customer ? String(txn.matched_customer) : "")}
                        onChange={(v) => setSelectedCustomer((prev) => ({ ...prev, [txn.id]: v }))}
                        placeholder="Select customer…"
                        emptyLabel="Clear customer"
                        hint={
                          // The guard stays even though /customers/picker/ is
                          // unpaginated and count always equals results.length
                          // now: if anyone ever paginates it, this is the only
                          // thing that would tell a user why a customer who
                          // exists "isn't there".
                          customersTruncated
                            ? "Search by name or payment reference. Not every customer is loaded here — if someone is missing, set their payment reference on the Customers page so future imports match them automatically."
                            : "Search by name or payment reference."
                        }
                      />
                    ) : (
                      <SearchableSelect
                        className="min-w-56"
                        options={supplierOptions}
                        value={selectedSupplier[txn.id] ?? (txn.matched_supplier ? String(txn.matched_supplier) : "")}
                        onChange={(v) => setSelectedSupplier((prev) => ({ ...prev, [txn.id]: v }))}
                        placeholder="Select supplier (optional)…"
                        emptyLabel="Clear supplier"
                      />
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
                          {isCredit ? (
                            <>
                              {(selectedCustomer[txn.id] && selectedCustomer[txn.id] !== String(txn.matched_customer)) && (
                                <button type="button" className={btnSecondary} disabled={busyId === txn.id} onClick={() => handleAssignCustomer(txn)}>
                                  Assign
                                </button>
                              )}
                              <button
                                type="button" className={btnPrimary} disabled={busyId === txn.id || (!txn.matched_customer && !selectedCustomer[txn.id])}
                                onClick={() => handleConfirmCredit(txn)}
                              >
                                Confirm
                              </button>
                            </>
                          ) : (
                            <>
                              {(selectedSupplier[txn.id] && selectedSupplier[txn.id] !== String(txn.matched_supplier)) && (
                                <button type="button" className={btnSecondary} disabled={busyId === txn.id} onClick={() => handleAssignSupplier(txn)}>
                                  Assign
                                </button>
                              )}
                              <button type="button" className={btnPrimary} disabled={busyId === txn.id} onClick={() => setExpenseModalTxn(txn)}>
                                Confirm as expense…
                              </button>
                            </>
                          )}
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
              );
            })}
            {items.length === 0 && <TR><TD className="text-[var(--text-muted)]">No transactions in this filter.</TD></TR>}
          </tbody>
        </Table>
        <Pager page={page} pageSize={REVIEW_PAGE_SIZE} count={count} shown={items.length} onPageChange={setPage} label="transactions" />
        </>
      )}

      {showImport && (
        <BankStatementImportModal accounts={accounts} onClose={() => setShowImport(false)} onImported={refetch} />
      )}

      {expenseModalTxn && (
        <ConfirmExpenseModal
          txn={expenseModalTxn}
          selectedSupplierId={selectedSupplier[expenseModalTxn.id]}
          onClose={() => setExpenseModalTxn(null)}
          onConfirmed={() => {
            setExpenseModalTxn(null);
            refetch();
          }}
        />
      )}
    </div>
  );
}

// --- Confirm-as-expense modal (debit transactions only) ---------------------

function ConfirmExpenseModal({
  txn, selectedSupplierId, onClose, onConfirmed,
}: {
  txn: BankTransaction; selectedSupplierId?: string; onClose: () => void; onConfirmed: () => void;
}) {
  const [category, setCategory] = useState<ExpenseCategory>("other");
  const [description, setDescription] = useState(txn.description);
  const [supplierName, setSupplierName] = useState("");
  // Deliberately no default VAT rate. A default of 15% meant a salary
  // run, an inter-account transfer or a loan repayment could be
  // click-through confirmed as carrying Input VAT that was never
  // charged -- SARS penalises overclaimed Input VAT, so the rate is an
  // explicit decision on every debit. The backend enforces the same rule.
  const [vatRate, setVatRate] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const totalInclVat = Math.abs(parseFloat(txn.amount));
  const parsedRate = parseFloat(vatRate);
  const rateIsValid = Number.isFinite(parsedRate) && parsedRate >= 0 && parsedRate <= 100;
  // Only computed once the rate is actually valid, so the preview can
  // never render "Infinity" or "NaN" the way it could when a rate of
  // -100 made the divisor zero.
  const amountExclVat = rateIsValid ? totalInclVat / (1 + parsedRate / 100) : null;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      const payload: Record<string, unknown> = {
        category, description, vat_rate_pct: vatRate,
      };
      const supplierId = selectedSupplierId || (txn.matched_supplier ? String(txn.matched_supplier) : "");
      if (supplierId) {
        payload.supplier = supplierId;
      } else if (supplierName) {
        payload.supplier_name = supplierName;
      } else {
        setError("Select a known supplier on the review row first, or type a supplier name below.");
        setSaving(false);
        return;
      }
      await api.post(`/bank-transactions/${txn.id}/confirm/`, payload);
      onConfirmed();
    } catch (err) {
      setError(apiErrorMessage(err, "Could not confirm this transaction as an expense."));
    } finally {
      setSaving(false);
    }
  }

  const hasKnownSupplier = Boolean(selectedSupplierId || txn.matched_supplier);

  return (
    <Modal title="Confirm as expense" onClose={onClose}>
      <p className="mb-3 text-sm text-[var(--text-secondary)]">
        {txn.date} — {txn.description} — R {totalInclVat.toFixed(2)} (total paid, VAT-inclusive)
      </p>
      <form onSubmit={handleSubmit}>
        {!hasKnownSupplier && (
          <FormField label="Supplier name (no matching Supplier record — type one, or cancel and pick one on the review row)">
            <input className={inputClass} value={supplierName} onChange={(e) => setSupplierName(e.target.value)} placeholder="e.g. Vodacom" />
          </FormField>
        )}
        <FormField label="Category">
          <select className={inputClass} value={category} onChange={(e) => setCategory(e.target.value as ExpenseCategory)}>
            {Object.entries(EXPENSE_CATEGORY_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </FormField>
        <FormField label="Description">
          <input className={inputClass} required value={description} onChange={(e) => setDescription(e.target.value)} />
        </FormField>
        <FormField label="VAT rate (%) — required">
          <input
            type="number" step="0.01" min="0" max="100" required className={inputClass} value={vatRate}
            onChange={(e) => setVatRate(e.target.value)}
            placeholder="15 for a normal VAT invoice, 0 if no VAT was charged"
          />
        </FormField>
        <p className="mb-3 text-xs text-[var(--text-muted)]">
          {amountExclVat === null ? (
            <>Enter the VAT rate on the supplier's invoice. Use <strong>0</strong> for anything that carries no VAT —
            salaries, transfers between your own accounts, loan repayments — otherwise this claims Input VAT you were
            never charged.</>
          ) : (
            <>Amount excl. VAT: R {amountExclVat.toFixed(2)} — VAT: R {(totalInclVat - amountExclVat).toFixed(2)}</>
          )}
        </p>

        {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}

        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className={btnSecondary} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={saving || !rateIsValid} className={btnPrimary}>
            {saving ? "Saving…" : "Confirm expense"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// --- CSV import modal (bespoke -- needs an account selector alongside the
// file, which the generic CSVImportModal doesn't support) --------------

function BankStatementImportModal({
  accounts, onClose, onImported,
}: {
  accounts: BankAccount[]; onClose: () => void; onImported: () => void;
}) {
  // Pre-selected when there is only one account, which is the usual case.
  // Nothing is gained by making someone pick from a list of one, and leaving
  // it unselected is what left Preview greyed out with no explanation.
  const [accountId, setAccountId] = useState(accounts.length === 1 ? String(accounts[0].id) : "");
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
    } catch (err) {
      // The backend now explains a whole-file failure (no header row found,
      // and what it saw instead). Show that rather than burying it under a
      // generic message the person can't act on.
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail || "Could not read that file. Make sure it's a CSV exported from FNB Online Banking.");
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

          {/* With no bank account on file the dropdown was empty and Preview
              was permanently greyed out, with nothing on screen saying why --
              it read as a broken import rather than a missing prerequisite.
              Say what's missing and where to fix it. */}
          {accounts.length === 0 ? (
            <p className="mb-4 rounded-md border border-[var(--status-warning)] bg-[#fff6e5] p-3 text-sm text-[#a5730a]">
              <strong>No bank account set up yet.</strong> A statement has to be imported against an account, so add
              one first: <strong>Accountant → Bank Feeds → Accounts → + New account</strong>. Only the name is
              required — the API fields are for the live feed and can be left blank for CSV imports.
            </p>
          ) : (
            <FormField label="Bank account">
              <select className={inputClass} required value={accountId} onChange={(e) => { setAccountId(e.target.value); setPreview(null); setResult(null); }}>
                <option value="">Select account…</option>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                    {a.account_number ? ` — ${a.account_number}` : ""}
                  </option>
                ))}
              </select>
            </FormField>
          )}

          <input
            type="file"
            accept=".csv,text/csv"
            className="mb-2 block w-full text-sm"
            onChange={(e) => { setFile(e.target.files?.[0] ?? null); setPreview(null); setResult(null); }}
          />

          {/* Why the button is dead, rather than leaving it to be guessed. */}
          {!preview && accounts.length > 0 && (!file || !accountId) && (
            <p className="mb-3 text-xs text-[var(--text-muted)]">
              {!accountId && !file
                ? "Choose an account and a CSV file to continue."
                : !accountId
                  ? "Choose which bank account this statement belongs to."
                  : "Choose the CSV file exported from FNB."}
            </p>
          )}

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
            {result.matched > 0 && <>, {result.matched} auto-matched</>}
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

// ---------------------------------------------------------------------------
// Clock in / out widget -- available to every staff member, in the header,
// on any of the staff-admin tabs below (moved here from the old Staff page,
// 2026-08-19).
// ---------------------------------------------------------------------------

function ClockInOutWidget({ onChange }: { onChange?: () => void }) {
  const [open, setOpen] = useState<AttendanceRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function refresh() {
    api
      .get<AttendanceRecord | null>("/attendance/open/")
      .then((r) => setOpen(r.data))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleClick() {
    setBusy(true);
    setError("");
    try {
      if (open) {
        await api.post("/attendance/clock_out/");
      } else {
        await api.post("/attendance/clock_in/");
      }
      refresh();
      onChange?.();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail || "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return null;

  return (
    <div className="flex items-center gap-2">
      {error && <span className="text-xs text-[var(--status-critical)]">{error}</span>}
      <button
        type="button"
        onClick={handleClick}
        disabled={busy}
        className={
          open
            ? "rounded-md bg-[var(--status-critical)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            : btnPrimary
        }
      >
        {busy ? "…" : open ? `Clock out (in since ${formatTime(open.clock_in)})` : "Clock in"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Attendance register
// ---------------------------------------------------------------------------

const EMPTY_ATTENDANCE = { staff: "", date: "", clock_in: "", clock_out: "", notes: "" };

function AttendanceTab({
  isAdmin,
  onRegisterNewAction,
  refreshSignal,
}: {
  isAdmin: boolean;
  onRegisterNewAction: (action: NewAction) => void;
  refreshSignal: number;
}) {
  const [staffFilter, setStaffFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [staffList, setStaffList] = useState<User[]>([]);
  const { items, loading, refetch } = useApiList<AttendanceRecord>(
    `/attendance/?page_size=200${staffFilter ? `&staff=${staffFilter}` : ""}${
      dateFrom ? `&date_from=${dateFrom}` : ""
    }${dateTo ? `&date_to=${dateTo}` : ""}`
  );

  useEffect(() => {
    if (refreshSignal > 0) refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshSignal]);
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<AttendanceRecord | null>(null);
  const [form, setForm] = useState(EMPTY_ATTENDANCE);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (isAdmin) {
      api.get<{ results: User[] }>("/staff-users/?page_size=200").then((r) => setStaffList(r.data.results));
    }
  }, [isAdmin]);

  useEffect(() => {
    if (!isAdmin) {
      onRegisterNewAction(null);
      return;
    }
    onRegisterNewAction({
      label: "+ Add entry",
      onClick: () => {
        setEditing(null);
        setForm(EMPTY_ATTENDANCE);
        setError("");
        setShowModal(true);
      },
    });
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin]);

  function openEdit(record: AttendanceRecord) {
    setEditing(record);
    setForm({
      staff: String(record.staff),
      date: record.date,
      clock_in: record.clock_in.slice(0, 16),
      clock_out: record.clock_out ? record.clock_out.slice(0, 16) : "",
      notes: record.notes,
    });
    setError("");
    setShowModal(true);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      const payload = {
        staff: Number(form.staff),
        date: form.date,
        clock_in: form.clock_in,
        clock_out: form.clock_out || null,
        notes: form.notes,
      };
      if (editing) {
        await api.patch(`/attendance/${editing.id}/`, payload);
      } else {
        await api.post("/attendance/", payload);
      }
      setShowModal(false);
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: unknown } })?.response?.data;
      setError(
        typeof detail === "string"
          ? detail
          : (detail as { detail?: string; non_field_errors?: string[] })?.detail ||
              (detail as { non_field_errors?: string[] })?.non_field_errors?.[0] ||
              "Failed to save attendance record."
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(record: AttendanceRecord) {
    if (!confirm(`Delete this attendance record for ${record.staff_name} on ${record.date}?`)) return;
    await api.delete(`/attendance/${record.id}/`);
    refetch();
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {isAdmin && (
          <select className={filterSelectClass} value={staffFilter} onChange={(e) => setStaffFilter(e.target.value)}>
            <option value="">All staff</option>
            {staffList.map((s) => (
              <option key={s.id} value={s.id}>
                {s.first_name || s.username} {s.last_name}
              </option>
            ))}
          </select>
        )}
        <FormField label="From">
          <input type="date" className={inputClass} value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        </FormField>
        <FormField label="To">
          <input type="date" className={inputClass} value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        </FormField>
        {(staffFilter || dateFrom || dateTo) && (
          <button
            type="button"
            className={`${btnSecondary} mb-3`}
            onClick={() => {
              setStaffFilter("");
              setDateFrom("");
              setDateTo("");
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
              <TH>Date</TH>
              {isAdmin && <TH>Staff</TH>}
              <TH>Clock in</TH>
              <TH>Clock out</TH>
              <TH>Worked hours</TH>
              <TH>Overtime</TH>
              <TH>Notes</TH>
              {isAdmin && <TH></TH>}
            </tr>
          </THead>
          <tbody>
            {items.map((r) => (
              <TR key={r.id}>
                <TD>{formatDate(r.date)}</TD>
                {isAdmin && <TD className="font-medium">{r.staff_name}</TD>}
                <TD>{formatTime(r.clock_in)}</TD>
                <TD>{r.clock_out ? formatTime(r.clock_out) : "—"}</TD>
                <TD className="tabular-nums">{r.worked_hours}</TD>
                <TD className="tabular-nums">
                  {Number(r.overtime_hours) > 0 ? (
                    <span className="font-medium text-[var(--status-warning)]">{r.overtime_hours}</span>
                  ) : (
                    "0.00"
                  )}
                </TD>
                <TD>{r.notes || "—"}</TD>
                {isAdmin && (
                  <TD>
                    <button className="text-xs text-[var(--series-1)] hover:underline" onClick={() => openEdit(r)}>
                      Edit
                    </button>
                    <button
                      className="ml-2 text-xs text-[var(--status-critical)] hover:underline"
                      onClick={() => handleDelete(r)}
                    >
                      Delete
                    </button>
                  </TD>
                )}
              </TR>
            ))}
            {items.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]" >No attendance records match the current filters.</TD>
              </TR>
            )}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title={editing ? "Edit attendance record" : "Add attendance record"} onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Staff member">
              <select
                className={inputClass}
                required
                value={form.staff}
                onChange={(e) => setForm({ ...form, staff: e.target.value })}
              >
                <option value="">Select staff…</option>
                {staffList.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.first_name || s.username} {s.last_name}
                  </option>
                ))}
              </select>
            </FormField>
            <FormField label="Date">
              <input
                type="date"
                className={inputClass}
                required
                value={form.date}
                onChange={(e) => setForm({ ...form, date: e.target.value })}
              />
            </FormField>
            <FormField label="Clock in">
              <input
                type="datetime-local"
                className={inputClass}
                required
                value={form.clock_in}
                onChange={(e) => setForm({ ...form, clock_in: e.target.value })}
              />
            </FormField>
            <FormField label="Clock out (optional)">
              <input
                type="datetime-local"
                className={inputClass}
                value={form.clock_out}
                onChange={(e) => setForm({ ...form, clock_out: e.target.value })}
              />
            </FormField>
            <FormField label="Notes (optional)">
              <input
                className={inputClass}
                value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
              />
            </FormField>
            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" className={btnPrimary} disabled={saving}>
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Leave (annual / sick / family responsibility)
// ---------------------------------------------------------------------------

const EMPTY_LEAVE = { staff: "", leave_type: "annual" as LeaveType, start_date: "", end_date: "", reason: "" };

function LeaveTab({
  isAdmin,
  onRegisterNewAction,
}: {
  isAdmin: boolean;
  onRegisterNewAction: (action: NewAction) => void;
}) {
  const [staffFilter, setStaffFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [staffList, setStaffList] = useState<User[]>([]);
  const [ownProfile, setOwnProfile] = useState<StaffProfile | null>(null);
  const { items, loading, refetch } = useApiList<LeaveRequest>(
    `/leave-requests/?page_size=200${staffFilter ? `&staff=${staffFilter}` : ""}${
      typeFilter ? `&leave_type=${typeFilter}` : ""
    }${statusFilter ? `&status=${statusFilter}` : ""}`
  );
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState(EMPTY_LEAVE);
  const [attachment, setAttachment] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [rejecting, setRejecting] = useState<LeaveRequest | null>(null);
  const [decisionNote, setDecisionNote] = useState("");

  useEffect(() => {
    if (isAdmin) {
      api.get<{ results: User[] }>("/staff-users/?page_size=200").then((r) => setStaffList(r.data.results));
    } else {
      api.get<StaffProfile | null>("/staff-profiles/me/").then((r) => setOwnProfile(r.data));
    }
  }, [isAdmin]);

  useEffect(() => {
    onRegisterNewAction({
      label: "+ Request leave",
      onClick: () => {
        setForm(EMPTY_LEAVE);
        setAttachment(null);
        setError("");
        setShowModal(true);
      },
    });
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      const fd = new FormData();
      if (isAdmin) fd.append("staff", form.staff);
      fd.append("leave_type", form.leave_type);
      fd.append("start_date", form.start_date);
      fd.append("end_date", form.end_date);
      fd.append("reason", form.reason);
      if (attachment) fd.append("attachment", attachment);
      await api.post("/leave-requests/", fd, { headers: { "Content-Type": "multipart/form-data" } });
      setShowModal(false);
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: unknown } })?.response?.data;
      setError(
        typeof detail === "string"
          ? detail
          : (detail as { non_field_errors?: string[] })?.non_field_errors?.[0] || "Failed to submit leave request."
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleWithdraw(leave: LeaveRequest) {
    if (!confirm(`Withdraw this ${LEAVE_TYPE_LABEL[leave.leave_type].toLowerCase()} request?`)) return;
    await api.delete(`/leave-requests/${leave.id}/`);
    refetch();
  }

  async function handleApprove(leave: LeaveRequest) {
    if (!confirm(`Approve ${leave.days_requested} day(s) of ${LEAVE_TYPE_LABEL[leave.leave_type].toLowerCase()} for ${leave.staff_name}?`))
      return;
    setBusyId(leave.id);
    try {
      await api.post(`/leave-requests/${leave.id}/approve/`);
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      alert(detail || "Failed to approve this request.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleReject(e: FormEvent) {
    e.preventDefault();
    if (!rejecting) return;
    setBusyId(rejecting.id);
    try {
      await api.post(`/leave-requests/${rejecting.id}/reject/`, { decision_note: decisionNote });
      setRejecting(null);
      setDecisionNote("");
      refetch();
    } finally {
      setBusyId(null);
    }
  }

  const ownBalance = form.leave_type && ownProfile ? ownProfile[LEAVE_BALANCE_FIELD[form.leave_type]] : null;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {isAdmin && (
          <select className={filterSelectClass} value={staffFilter} onChange={(e) => setStaffFilter(e.target.value)}>
            <option value="">All staff</option>
            {staffList.map((s) => (
              <option key={s.id} value={s.id}>
                {s.first_name || s.username} {s.last_name}
              </option>
            ))}
          </select>
        )}
        <select className={filterSelectClass} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          <option value="">All leave types</option>
          {Object.entries(LEAVE_TYPE_LABEL).map(([v, l]) => (
            <option key={v} value={v}>{l}</option>
          ))}
        </select>
        <select className={filterSelectClass} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          <option value="pending">Pending</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
        </select>
        {(staffFilter || typeFilter || statusFilter) && (
          <button
            type="button"
            className={btnSecondary}
            onClick={() => {
              setStaffFilter("");
              setTypeFilter("");
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
              {isAdmin && <TH>Staff</TH>}
              <TH>Type</TH>
              <TH>Dates</TH>
              <TH>Days</TH>
              <TH>Reason</TH>
              <TH>Letter</TH>
              <TH>Status</TH>
              <TH>Decided by</TH>
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((l) => (
              <TR key={l.id}>
                {isAdmin && <TD className="font-medium">{l.staff_name}</TD>}
                <TD>{LEAVE_TYPE_LABEL[l.leave_type]}</TD>
                <TD>
                  {formatDate(l.start_date)} – {formatDate(l.end_date)}
                </TD>
                <TD className="tabular-nums">{l.days_requested}</TD>
                <TD>{l.reason || "—"}</TD>
                <TD>
                  {l.attachment ? (
                    <a href={l.attachment} target="_blank" rel="noreferrer" className="text-[var(--series-1)] hover:underline">
                      View
                    </a>
                  ) : (
                    "—"
                  )}
                </TD>
                <TD>
                  <StatusBadge status={l.status} />
                  {l.status === "rejected" && l.decision_note && (
                    <div className="mt-1 text-xs text-[var(--text-muted)]">{l.decision_note}</div>
                  )}
                </TD>
                <TD>{l.decided_by_name || "—"}</TD>
                <TD>
                  {l.status === "pending" && isAdmin && (
                    <>
                      <button
                        className="text-xs text-[var(--series-1)] hover:underline"
                        disabled={busyId === l.id}
                        onClick={() => handleApprove(l)}
                      >
                        Approve
                      </button>
                      <button
                        className="ml-2 text-xs text-[var(--status-critical)] hover:underline"
                        disabled={busyId === l.id}
                        onClick={() => {
                          setRejecting(l);
                          setDecisionNote("");
                        }}
                      >
                        Reject
                      </button>
                    </>
                  )}
                  {l.status === "pending" && (
                    <button
                      className="ml-2 text-xs text-[var(--status-critical)] hover:underline"
                      onClick={() => handleWithdraw(l)}
                    >
                      Withdraw
                    </button>
                  )}
                </TD>
              </TR>
            ))}
            {items.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]">No leave requests match the current filters.</TD>
              </TR>
            )}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title="Request leave" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            {isAdmin && (
              <FormField label="Staff member">
                <select
                  className={inputClass}
                  required
                  value={form.staff}
                  onChange={(e) => setForm({ ...form, staff: e.target.value })}
                >
                  <option value="">Select staff…</option>
                  {staffList.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.first_name || s.username} {s.last_name}
                    </option>
                  ))}
                </select>
              </FormField>
            )}
            <FormField label="Leave type">
              <select
                className={inputClass}
                value={form.leave_type}
                onChange={(e) => setForm({ ...form, leave_type: e.target.value as LeaveType })}
              >
                {Object.entries(LEAVE_TYPE_LABEL).map(([v, l]) => (
                  <option key={v} value={v}>{l}</option>
                ))}
              </select>
            </FormField>
            {ownBalance != null && (
              <p className="mb-3 text-xs text-[var(--text-muted)]">
                You have {ownBalance} day(s) of {LEAVE_TYPE_LABEL[form.leave_type].toLowerCase()} remaining.
              </p>
            )}
            <FormField label="Start date">
              <input
                type="date"
                className={inputClass}
                required
                value={form.start_date}
                onChange={(e) => setForm({ ...form, start_date: e.target.value })}
              />
            </FormField>
            <FormField label="End date">
              <input
                type="date"
                className={inputClass}
                required
                value={form.end_date}
                onChange={(e) => setForm({ ...form, end_date: e.target.value })}
              />
            </FormField>
            <FormField label="Reason (optional)">
              <input
                className={inputClass}
                value={form.reason}
                onChange={(e) => setForm({ ...form, reason: e.target.value })}
              />
            </FormField>
            {form.leave_type === "sick" && (
              <FormField label="Sick leave letter / certificate (optional)">
                <input
                  type="file"
                  accept=".pdf,.png,.jpg,.jpeg,.webp,.heic,.gif,.txt"
                  className={inputClass}
                  onChange={(e) => setAttachment(e.target.files?.[0] ?? null)}
                />
              </FormField>
            )}
            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" className={btnPrimary} disabled={saving}>
                {saving ? "Submitting…" : "Submit request"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {rejecting && (
        <Modal title="Reject leave request" onClose={() => setRejecting(null)}>
          <form onSubmit={handleReject}>
            <p className="mb-3 text-sm text-[var(--text-secondary)]">
              Rejecting {rejecting.staff_name}'s {LEAVE_TYPE_LABEL[rejecting.leave_type].toLowerCase()} request (
              {formatDate(rejecting.start_date)} – {formatDate(rejecting.end_date)}).
            </p>
            <FormField label="Reason (optional)">
              <input
                className={inputClass}
                value={decisionNote}
                onChange={(e) => setDecisionNote(e.target.value)}
              />
            </FormField>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setRejecting(null)}>
                Cancel
              </button>
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
// Employees (staff pay configuration)
// ---------------------------------------------------------------------------

const EMPTY_PROFILE_FORM = {
  user: "",
  id_number: "",
  license_number: "",
  pay_type: "hourly" as PayType,
  monthly_salary: "",
  hourly_rate: "",
  standard_daily_hours: "8.00",
  overtime_multiplier: "1.50",
  annual_leave_balance: "21.0",
  sick_leave_balance: "30.0",
  family_responsibility_leave_balance: "3.0",
  is_active: true,
};

function EmployeesTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const { items, loading, refetch } = useApiList<StaffProfile>("/staff-profiles/?page_size=200");
  const [staffList, setStaffList] = useState<User[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<StaffProfile | null>(null);
  const [form, setForm] = useState(EMPTY_PROFILE_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get<{ results: User[] }>("/staff-users/?page_size=200").then((r) => setStaffList(r.data.results));
  }, []);

  useEffect(() => {
    onRegisterNewAction({
      label: "+ Add employee",
      onClick: () => {
        setEditing(null);
        setForm(EMPTY_PROFILE_FORM);
        setError("");
        setShowModal(true);
      },
    });
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const eligibleStaff = staffList.filter((s) => !items.some((p) => p.user === s.id));

  function openEdit(profile: StaffProfile) {
    setEditing(profile);
    setForm({
      user: String(profile.user),
      id_number: profile.id_number,
      license_number: profile.license_number,
      pay_type: profile.pay_type,
      monthly_salary: profile.monthly_salary || "",
      hourly_rate: profile.hourly_rate || "",
      standard_daily_hours: profile.standard_daily_hours,
      overtime_multiplier: profile.overtime_multiplier,
      annual_leave_balance: profile.annual_leave_balance,
      sick_leave_balance: profile.sick_leave_balance,
      family_responsibility_leave_balance: profile.family_responsibility_leave_balance,
      is_active: profile.is_active,
    });
    setError("");
    setShowModal(true);
  }

  async function handleDelete(profile: StaffProfile) {
    if (
      !confirm(
        `Remove ${profile.staff_name} (${profile.employee_number}) from payroll? Their past attendance and payroll history are kept, but they'll be excluded from future payroll runs until re-added.`
      )
    )
      return;
    await api.delete(`/staff-profiles/${profile.id}/`);
    refetch();
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      const payload = {
        user: Number(form.user),
        id_number: form.id_number,
        license_number: form.license_number,
        pay_type: form.pay_type,
        monthly_salary: form.pay_type === "salary" ? form.monthly_salary || null : null,
        hourly_rate: form.pay_type === "hourly" ? form.hourly_rate || null : null,
        standard_daily_hours: form.standard_daily_hours,
        overtime_multiplier: form.overtime_multiplier,
        annual_leave_balance: form.annual_leave_balance,
        sick_leave_balance: form.sick_leave_balance,
        family_responsibility_leave_balance: form.family_responsibility_leave_balance,
        is_active: form.is_active,
      };
      if (editing) {
        await api.patch(`/staff-profiles/${editing.id}/`, payload);
      } else {
        await api.post("/staff-profiles/", payload);
      }
      setShowModal(false);
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: unknown } })?.response?.data;
      setError(
        typeof detail === "string"
          ? detail
          : (detail as { non_field_errors?: string[] })?.non_field_errors?.[0] ||
              JSON.stringify(detail) ||
              "Failed to save employee."
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Employee #</TH>
              <TH>Name</TH>
              <TH>ID number</TH>
              <TH>License number</TH>
              <TH>Contact number</TH>
              <TH>Role</TH>
              <TH>Pay type</TH>
              <TH>Rate</TH>
              <TH>Daily hours</TH>
              <TH>OT multiplier</TH>
              <TH>Leave balance (Annual/Sick/Family)</TH>
              <TH>Status</TH>
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((p) => (
              <TR key={p.id}>
                <TD className="font-medium">{p.employee_number}</TD>
                <TD>{p.staff_name}</TD>
                <TD>{p.id_number || "—"}</TD>
                <TD>{p.license_number || "—"}</TD>
                <TD>{p.phone || "—"}</TD>
                <TD className="capitalize">{p.role}</TD>
                <TD>{PAY_TYPE_LABEL[p.pay_type]}</TD>
                <TD className="tabular-nums">
                  {p.pay_type === "salary" ? `R ${p.monthly_salary}/mo` : `R ${p.hourly_rate}/hr`}
                </TD>
                <TD className="tabular-nums">{p.standard_daily_hours}</TD>
                <TD className="tabular-nums">{p.overtime_multiplier}x</TD>
                <TD className="tabular-nums">
                  {p.annual_leave_balance} / {p.sick_leave_balance} / {p.family_responsibility_leave_balance}
                </TD>
                <TD>
                  <StatusBadge status={p.is_active ? "active" : "inactive"} />
                </TD>
                <TD>
                  <button className="text-xs text-[var(--series-1)] hover:underline" onClick={() => openEdit(p)}>
                    Edit
                  </button>
                  <button
                    className="ml-2 text-xs text-[var(--status-critical)] hover:underline"
                    onClick={() => handleDelete(p)}
                  >
                    Delete
                  </button>
                </TD>
              </TR>
            ))}
            {items.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]">No employees configured for payroll yet.</TD>
              </TR>
            )}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title={editing ? "Edit employee" : "Add employee"} onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            {!editing && (
              <FormField label="Staff member">
                <select
                  className={inputClass}
                  required
                  value={form.user}
                  onChange={(e) => setForm({ ...form, user: e.target.value })}
                >
                  <option value="">Select staff…</option>
                  {eligibleStaff.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.first_name || s.username} {s.last_name} ({s.role})
                    </option>
                  ))}
                </select>
              </FormField>
            )}
            <FormField label="ID number">
              <input
                className={inputClass}
                placeholder="National ID / passport number"
                value={form.id_number}
                onChange={(e) => setForm({ ...form, id_number: e.target.value })}
              />
            </FormField>
            <FormField label="License number">
              <input
                className={inputClass}
                placeholder="Driver's license number"
                value={form.license_number}
                onChange={(e) => setForm({ ...form, license_number: e.target.value })}
              />
            </FormField>
            <FormField label="Pay type">
              <select
                className={inputClass}
                value={form.pay_type}
                onChange={(e) => setForm({ ...form, pay_type: e.target.value as PayType })}
              >
                <option value="hourly">Hourly rate</option>
                <option value="salary">Monthly salary</option>
              </select>
            </FormField>
            {form.pay_type === "salary" ? (
              <FormField label="Monthly salary (R)">
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  className={inputClass}
                  required
                  value={form.monthly_salary}
                  onChange={(e) => setForm({ ...form, monthly_salary: e.target.value })}
                />
              </FormField>
            ) : (
              <FormField label="Hourly rate (R)">
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  className={inputClass}
                  required
                  value={form.hourly_rate}
                  onChange={(e) => setForm({ ...form, hourly_rate: e.target.value })}
                />
              </FormField>
            )}
            <FormField label="Standard hours per day (before overtime)">
              <input
                type="number"
                step="0.25"
                min="1"
                className={inputClass}
                required
                value={form.standard_daily_hours}
                onChange={(e) => setForm({ ...form, standard_daily_hours: e.target.value })}
              />
            </FormField>
            <FormField label="Overtime rate multiplier">
              <input
                type="number"
                step="0.05"
                min="1"
                className={inputClass}
                required
                value={form.overtime_multiplier}
                onChange={(e) => setForm({ ...form, overtime_multiplier: e.target.value })}
              />
            </FormField>
            <div className="mb-1 mt-2 text-sm font-medium text-[var(--text-secondary)]">Leave balances (days)</div>
            <div className="mb-3 grid grid-cols-3 gap-2">
              <FormField label="Annual">
                <input
                  type="number"
                  step="0.5"
                  min="0"
                  className={inputClass}
                  required
                  value={form.annual_leave_balance}
                  onChange={(e) => setForm({ ...form, annual_leave_balance: e.target.value })}
                />
              </FormField>
              <FormField label="Sick">
                <input
                  type="number"
                  step="0.5"
                  min="0"
                  className={inputClass}
                  required
                  value={form.sick_leave_balance}
                  onChange={(e) => setForm({ ...form, sick_leave_balance: e.target.value })}
                />
              </FormField>
              <FormField label="Family resp.">
                <input
                  type="number"
                  step="0.5"
                  min="0"
                  className={inputClass}
                  required
                  value={form.family_responsibility_leave_balance}
                  onChange={(e) => setForm({ ...form, family_responsibility_leave_balance: e.target.value })}
                />
              </FormField>
            </div>
            <label className="mb-3 flex items-center gap-2 text-sm text-[var(--text-primary)]">
              <input
                type="checkbox"
                checked={form.is_active}
                onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
              />
              Active (included in payroll runs)
            </label>
            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" className={btnPrimary} disabled={saving}>
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Payroll runs
// ---------------------------------------------------------------------------


// --- Payroll settings -------------------------------------------------------
//
// Every number here is set by legislation and moves. Hardcoding the UIF
// ceiling would mean silently under-deducting from the month it changed, with
// nothing on screen to say so — hence a form, and hence the warning on it.

function PayrollSettingsCard() {
  const [form, setForm] = useState<PayrollSettingsConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get<PayrollSettingsConfig>("/payroll-settings/").then((r) => setForm(r.data));
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!form) return;
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      const res = await api.patch<PayrollSettingsConfig>("/payroll-settings/", form);
      setForm(res.data);
      setSaved(true);
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const first = data ? Object.values(data).flat()[0] : null;
      setError(typeof first === "string" ? first : "Could not save these settings.");
    } finally {
      setSaving(false);
    }
  }

  if (!form) return null;

  return (
    <form
      onSubmit={handleSubmit}
      className="mt-6 max-w-2xl rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5"
    >
      <h2 className="mb-1 text-sm font-semibold text-[var(--text-primary)]">Payroll settings</h2>
      <p className="mb-3 text-sm text-[var(--text-secondary)]">
        Printed on every payslip, and used to calculate UIF and SDL.
      </p>
      <p className="mb-4 rounded-md border border-[var(--status-warning)] bg-[#fff6e5] p-2 text-xs text-[#a5730a]">
        <strong>Check these against the current official figures before running a real payroll.</strong> The
        defaults were correct when this was built, but the UIF ceiling in particular is revised periodically —
        an out-of-date value under-deducts silently. PAYE is not here because it isn't calculated: it's entered
        per employee on each payroll line.
      </p>

      <FormField label="Employer name (as it appears on the payslip)">
        <input
          className={inputClass}
          value={form.employer_name}
          onChange={(e) => setForm({ ...form, employer_name: e.target.value })}
        />
      </FormField>
      <FormField label="Employer address">
        <input
          className={inputClass}
          value={form.employer_address}
          onChange={(e) => setForm({ ...form, employer_address: e.target.value })}
        />
      </FormField>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <FormField label="SARS PAYE reference">
          <input
            className={inputClass}
            value={form.paye_reference}
            onChange={(e) => setForm({ ...form, paye_reference: e.target.value })}
          />
        </FormField>
        <FormField label="UIF reference">
          <input
            className={inputClass}
            value={form.uif_reference}
            onChange={(e) => setForm({ ...form, uif_reference: e.target.value })}
          />
        </FormField>
      </div>

      <h3 className="mb-2 mt-4 text-sm font-semibold text-[var(--text-primary)]">UIF</h3>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <FormField label="Employee rate (%)">
          <input
            type="number" step="0.01" className={inputClass}
            value={form.uif_rate_pct}
            onChange={(e) => setForm({ ...form, uif_rate_pct: e.target.value })}
          />
        </FormField>
        <FormField label="Monthly ceiling (R)">
          <input
            type="number" step="0.01" className={inputClass}
            value={form.uif_monthly_ceiling}
            onChange={(e) => setForm({ ...form, uif_monthly_ceiling: e.target.value })}
          />
        </FormField>
      </div>
      <p className="-mt-1 mb-3 text-xs text-[var(--text-muted)]">
        Charged on remuneration up to the ceiling only, so a salary above it contributes a flat amount. The
        employer contributes the same again — that shows on the payslip but isn't deducted from the employee.
      </p>

      <h3 className="mb-2 mt-4 text-sm font-semibold text-[var(--text-primary)]">SDL</h3>
      <label className="mb-2 flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          className="mt-1"
          checked={form.sdl_applicable}
          onChange={(e) => setForm({ ...form, sdl_applicable: e.target.checked })}
        />
        <span className="text-[var(--text-secondary)]">
          We're registered for the Skills Development Levy. Off unless your annual payroll is over the
          registration threshold — leave it off if you're not sure, and check with your accountant.
        </span>
      </label>
      {form.sdl_applicable && (
        <FormField label="SDL rate (%)">
          <input
            type="number" step="0.01" className={inputClass}
            value={form.sdl_rate_pct}
            onChange={(e) => setForm({ ...form, sdl_rate_pct: e.target.value })}
          />
        </FormField>
      )}

      <FormField label="Note at the bottom of every payslip (optional)">
        <input
          className={inputClass}
          placeholder="e.g. Queries: accounts@skybre.co.za"
          value={form.payslip_note}
          onChange={(e) => setForm({ ...form, payslip_note: e.target.value })}
        />
      </FormField>

      {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
      {saved && !error && <p className="mb-3 text-sm text-[#0ca30c]">Settings saved.</p>}
      <div className="flex justify-end">
        <button type="submit" className={btnPrimary} disabled={saving}>
          {saving ? "Saving…" : "Save payroll settings"}
        </button>
      </div>
    </form>
  );
}

function PayrollTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const { items, loading, refetch } = useApiList<PayrollRun>("/payroll-runs/?page_size=100");
  const [showModal, setShowModal] = useState(false);
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<PayrollRun | null>(null);
  const [busyAction, setBusyAction] = useState(false);
  // Recalculate/Finalize/Delete happen outside the modal, where the
  // existing `error` state is never rendered -- so their failures had
  // nowhere to appear at all.
  const [actionError, setActionError] = useState("");
  const [payslipPreview, setPayslipPreview] = useState<{ url: string; title: string; filename: string } | null>(null);
  // The line being edited: PAYE and the other typed-in figures.
  const [editingLine, setEditingLine] = useState<PayrollRunLine | null>(null);
  const [lineForm, setLineForm] = useState({
    paye: "", additional_amount: "", additional_description: "",
    other_deduction_amount: "", other_deduction_description: "", notes: "",
  });
  const [lineSaving, setLineSaving] = useState(false);
  const [lineError, setLineError] = useState("");
  const [emailingLineId, setEmailingLineId] = useState<number | null>(null);
  const [emailResult, setEmailResult] = useState("");

  function openLineEdit(line: PayrollRunLine) {
    setEditingLine(line);
    setLineForm({
      paye: line.paye,
      additional_amount: line.additional_amount,
      additional_description: line.additional_description,
      other_deduction_amount: line.other_deduction_amount,
      other_deduction_description: line.other_deduction_description,
      notes: line.notes,
    });
    setLineError("");
  }

  async function handleLineSave(e: FormEvent) {
    e.preventDefault();
    if (!editingLine) return;
    setLineSaving(true);
    setLineError("");
    try {
      await api.patch(`/payroll-run-lines/${editingLine.id}/`, lineForm);
      setEditingLine(null);
      // Refetch the run, not just the line: UIF moves with the extra amount,
      // and the run totals move with all of it.
      const res = await api.get<PayrollRun>(`/payroll-runs/${selected?.id}/`);
      setSelected(res.data);
      refetch();
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const first = data ? Object.values(data).flat()[0] : null;
      setLineError(typeof first === "string" ? first : "Could not save this line.");
    } finally {
      setLineSaving(false);
    }
  }

  async function handleEmailPayslip(line: PayrollRunLine) {
    if (!confirm(`Email this payslip to ${line.staff_name}?`)) return;
    setEmailingLineId(line.id);
    setEmailResult("");
    try {
      const res = await api.post<{ detail: string }>(`/payroll-run-lines/${line.id}/email-payslip/`);
      setEmailResult(res.data.detail);
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setEmailResult(detail || "Could not send that payslip.");
    } finally {
      setEmailingLineId(null);
    }
  }

  useEffect(() => {
    onRegisterNewAction({
      label: "+ New payroll run",
      onClick: () => {
        setPeriodStart("");
        setPeriodEnd("");
        setError("");
        setShowModal(true);
      },
    });
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      const res = await api.post<PayrollRun>("/payroll-runs/", {
        period_start: periodStart,
        period_end: periodEnd,
      });
      setShowModal(false);
      refetch();
      setSelected(res.data);
    } catch (err) {
      const detail = (err as { response?: { data?: unknown } })?.response?.data;
      setError(
        typeof detail === "string"
          ? detail
          : (detail as { non_field_errors?: string[] })?.non_field_errors?.[0] || "Failed to generate payroll run."
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleRecalculate(run: PayrollRun) {
    setBusyAction(true);
    try {
      const res = await api.post<PayrollRun>(`/payroll-runs/${run.id}/recalculate/`);
      setSelected(res.data);
      refetch();
    } catch (err) {
      setActionError(apiErrorMessage(err, "Could not recalculate this payroll run."));
    } finally {
      setBusyAction(false);
    }
  }

  async function handleFinalize(run: PayrollRun) {
    if (!confirm("Finalize this payroll run? It can no longer be recalculated afterwards.")) return;
    setBusyAction(true);
    try {
      const res = await api.post<PayrollRun>(`/payroll-runs/${run.id}/finalize/`);
      setSelected(res.data);
      refetch();
    } catch (err) {
      // A rejected Finalize used to leave the run showing "draft" with no
      // explanation at all.
      setActionError(apiErrorMessage(err, "Could not finalize this payroll run."));
    } finally {
      setBusyAction(false);
    }
  }

  async function handleExport(run: PayrollRun) {
    const res = await api.get(`/payroll-runs/${run.id}/export_csv/`, { responseType: "blob" });
    const url = window.URL.createObjectURL(new Blob([res.data]));
    const link = document.createElement("a");
    link.href = url;
    link.download = `payroll_${run.period_start}_${run.period_end}.csv`;
    link.click();
    window.URL.revokeObjectURL(url);
  }

  async function handleDelete(run: PayrollRun) {
    if (!confirm(`Delete the payroll run for ${formatDate(run.period_start)} – ${formatDate(run.period_end)}?`)) return;
    setBusyAction(true);
    try {
      await api.delete(`/payroll-runs/${run.id}/`);
      if (selected?.id === run.id) setSelected(null);
      refetch();
    } finally {
      setBusyAction(false);
    }
  }

  return (
    <div>
      {actionError && (
        <p className="mb-3 rounded-md border border-[var(--status-critical)] bg-[var(--tint-subtle)] p-2 text-sm text-[var(--status-critical)]">
          {actionError}
        </p>
      )}
      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Period</TH>
              <TH>Status</TH>
              <TH>Staff</TH>
              <TH>Regular hours</TH>
              <TH>Overtime hours</TH>
              <TH>Gross pay</TH>
              <TH>Created</TH>
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((run) => (
              <TR key={run.id} onClick={() => setSelected(run)}>
                <TD className="font-medium">
                  {formatDate(run.period_start)} – {formatDate(run.period_end)}
                </TD>
                <TD>
                  <StatusBadge status={run.status} />
                </TD>
                <TD className="tabular-nums">{run.staff_count}</TD>
                <TD className="tabular-nums">{run.total_regular_hours}</TD>
                <TD className="tabular-nums">{run.total_overtime_hours}</TD>
                <TD className="tabular-nums">R {run.total_gross_pay}</TD>
                <TD>{formatDate(run.created_at)}</TD>
                <TD>
                  {run.status === "draft" && (
                    <button
                      className="text-xs text-[var(--status-critical)] hover:underline"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(run);
                      }}
                    >
                      Delete
                    </button>
                  )}
                </TD>
              </TR>
            ))}
            {items.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]">No payroll runs yet.</TD>
              </TR>
            )}
          </tbody>
        </Table>
      )}

      {selected && (
        <div className="mt-6 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="text-sm font-semibold text-[var(--text-primary)]">
                Payroll: {formatDate(selected.period_start)} – {formatDate(selected.period_end)}
              </h3>
              <div className="mt-1"><StatusBadge status={selected.status} /></div>
            </div>
            <div className="flex flex-wrap gap-2">
              <button className={btnSecondary} disabled={busyAction} onClick={() => handleExport(selected)}>
                Export CSV
              </button>
              <button
                className={btnSecondary}
                onClick={() =>
                  setPayslipPreview({
                    url: `/payroll-runs/${selected.id}/payslips-pdf/`,
                    title: `All payslips — ${formatDate(selected.period_start)} to ${formatDate(selected.period_end)}`,
                    filename: `payslips-${selected.period_start}-${selected.period_end}.pdf`,
                  })
                }
              >
                All payslips
              </button>
              {selected.status === "draft" && (
                <>
                  <button className={btnSecondary} disabled={busyAction} onClick={() => handleRecalculate(selected)}>
                    Recalculate
                  </button>
                  <button
                    className="rounded-md border border-[var(--status-critical)] px-4 py-2 text-sm font-medium text-[var(--status-critical)] hover:bg-[var(--tint-hover)] disabled:opacity-50"
                    disabled={busyAction}
                    onClick={() => handleDelete(selected)}
                  >
                    Delete
                  </button>
                  <button className={btnPrimary} disabled={busyAction} onClick={() => handleFinalize(selected)}>
                    Finalize
                  </button>
                </>
              )}
              <button className={btnSecondary} onClick={() => setSelected(null)}>
                Close
              </button>
            </div>
          </div>
          {emailResult && (
            <p className="mb-3 text-sm text-[var(--text-secondary)]">{emailResult}</p>
          )}
          {selected.status === "draft" && (
            <p className="mb-3 text-xs text-[var(--text-muted)]">
              Payslips can be previewed while this run is a draft — they print marked DRAFT. Finalise the run to
              email them.
            </p>
          )}
          <Table>
            <THead>
              <tr>
                <TH>Employee #</TH>
                <TH>Name</TH>
                <TH>Pay type</TH>
                <TH>Regular hours</TH>
                <TH>Overtime hours</TH>
                <TH>Rate</TH>
                <TH>OT rate</TH>
                <TH>Gross pay</TH>
                <TH>Extra</TH>
                <TH>PAYE</TH>
                <TH>UIF</TH>
                <TH>Other</TH>
                <TH>Net pay</TH>
                <TH></TH>
              </tr>
            </THead>
            <tbody>
              {selected.lines.map((line) => (
                <TR key={line.id}>
                  <TD>{line.employee_number}</TD>
                  <TD className="font-medium">{line.staff_name}</TD>
                  <TD>{PAY_TYPE_LABEL[line.pay_type]}</TD>
                  <TD className="tabular-nums">{line.regular_hours}</TD>
                  <TD className="tabular-nums">{line.overtime_hours}</TD>
                  <TD className="tabular-nums">R {line.hourly_rate}</TD>
                  <TD className="tabular-nums">R {line.overtime_rate}</TD>
                  <TD className="tabular-nums">R {line.gross_pay}</TD>
                  <TD className="tabular-nums">
                    {parseFloat(line.additional_amount) ? (
                      <span title={line.additional_description}>R {line.additional_amount}</span>
                    ) : (
                      <span className="text-[var(--text-muted)]">—</span>
                    )}
                  </TD>
                  <TD className="tabular-nums">
                    {parseFloat(line.paye) ? (
                      `R ${line.paye}`
                    ) : (
                      // Not an error, but worth flagging: a zero PAYE on
                      // someone earning is nearly always "nobody has entered
                      // it yet" rather than a real nil deduction.
                      <span className="text-[var(--status-warning)]" title="No PAYE entered for this employee">
                        not entered
                      </span>
                    )}
                  </TD>
                  <TD className="tabular-nums">R {line.uif_employee}</TD>
                  <TD className="tabular-nums">
                    {parseFloat(line.other_deduction_amount) ? (
                      <span title={line.other_deduction_description}>R {line.other_deduction_amount}</span>
                    ) : (
                      <span className="text-[var(--text-muted)]">—</span>
                    )}
                  </TD>
                  <TD className="tabular-nums font-semibold">R {line.net_pay}</TD>
                  <TD>
                    <div className="flex flex-wrap gap-2">
                      {selected.status === "draft" && (
                        <button
                          className="text-xs text-[var(--series-1)] hover:underline"
                          onClick={() => openLineEdit(line)}
                        >
                          Edit
                        </button>
                      )}
                      <button
                        className="text-xs text-[var(--series-1)] hover:underline"
                        onClick={() =>
                          setPayslipPreview({
                            url: `/payroll-run-lines/${line.id}/payslip-pdf/`,
                            title: `Payslip — ${line.staff_name}`,
                            filename: `payslip-${line.staff_name.replace(/\s+/g, "-")}-${selected.period_end}.pdf`,
                          })
                        }
                      >
                        Payslip
                      </button>
                      {selected.status === "finalized" && (
                        <button
                          className="text-xs text-[var(--series-1)] hover:underline disabled:opacity-40"
                          disabled={emailingLineId === line.id}
                          onClick={() => handleEmailPayslip(line)}
                        >
                          {emailingLineId === line.id ? "Sending…" : "Email"}
                        </button>
                      )}
                    </div>
                  </TD>
                </TR>
              ))}
              {selected.lines.length === 0 && (
                <TR>
                  <TD className="text-[var(--text-muted)]">
                    No active employees with configured pay had matching attendance in this period.
                  </TD>
                </TR>
              )}
            </tbody>
          </Table>
        </div>
      )}

      {payslipPreview && (
        <PdfPreviewModal
          title={payslipPreview.title}
          url={payslipPreview.url}
          filename={payslipPreview.filename}
          onClose={() => setPayslipPreview(null)}
        />
      )}

      {editingLine && (
        <Modal title={`Deductions — ${editingLine.staff_name}`} onClose={() => setEditingLine(null)}>
          <form onSubmit={handleLineSave}>
            <p className="mb-3 text-sm text-[var(--text-secondary)]">
              Gross pay <strong>R {editingLine.gross_pay}</strong> · UIF{" "}
              <strong>R {editingLine.uif_employee}</strong> (calculated)
            </p>
            {/* Said plainly, because a wrong statutory figure on a payslip is
                a legal problem rather than a bug report. */}
            <p className="mb-3 rounded-md border border-[var(--status-warning)] bg-[#fff6e5] p-2 text-xs text-[#a5730a]">
              <strong>PAYE is not calculated here.</strong> It depends on the current SARS tax tables, the
              employee's age rebate and any medical aid credits — enter the figure from your accountant or SARS
              eFiling. UIF and SDL <em>are</em> calculated, from the rates under Payroll settings.
            </p>
            <FormField label="PAYE (income tax withheld)">
              <input
                type="number" step="0.01" className={inputClass}
                value={lineForm.paye}
                onChange={(e) => setLineForm({ ...lineForm, paye: e.target.value })}
              />
            </FormField>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <FormField label="Extra payment">
                <input
                  type="number" step="0.01" className={inputClass}
                  value={lineForm.additional_amount}
                  onChange={(e) => setLineForm({ ...lineForm, additional_amount: e.target.value })}
                />
              </FormField>
              <FormField label="What it's for">
                <input
                  className={inputClass} placeholder="e.g. December bonus"
                  value={lineForm.additional_description}
                  onChange={(e) => setLineForm({ ...lineForm, additional_description: e.target.value })}
                />
              </FormField>
            </div>
            <p className="-mt-1 mb-3 text-xs text-[var(--text-muted)]">
              An extra payment is remuneration, so UIF is recalculated on it when you save.
            </p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <FormField label="Other deduction">
                <input
                  type="number" step="0.01" className={inputClass}
                  value={lineForm.other_deduction_amount}
                  onChange={(e) => setLineForm({ ...lineForm, other_deduction_amount: e.target.value })}
                />
              </FormField>
              <FormField label="What it's for">
                <input
                  className={inputClass} placeholder="e.g. Loan repayment"
                  value={lineForm.other_deduction_description}
                  onChange={(e) => setLineForm({ ...lineForm, other_deduction_description: e.target.value })}
                />
              </FormField>
            </div>
            <FormField label="Note on the payslip (optional)">
              <input
                className={inputClass}
                value={lineForm.notes}
                onChange={(e) => setLineForm({ ...lineForm, notes: e.target.value })}
              />
            </FormField>
            {lineError && <p className="mb-3 text-sm text-[var(--status-critical)]">{lineError}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setEditingLine(null)}>Cancel</button>
              <button type="submit" className={btnPrimary} disabled={lineSaving}>
                {lineSaving ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      <PayrollSettingsCard />

      {showModal && (
        <Modal title="Generate payroll run" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Period start">
              <input
                type="date"
                className={inputClass}
                required
                value={periodStart}
                onChange={(e) => setPeriodStart(e.target.value)}
              />
            </FormField>
            <FormField label="Period end">
              <input
                type="date"
                className={inputClass}
                required
                value={periodEnd}
                onChange={(e) => setPeriodEnd(e.target.value)}
              />
            </FormField>
            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
            <p className="mb-3 text-xs text-[var(--text-muted)]">
              Hours and overtime are calculated from clocked attendance in this range; salaried staff are paid their
              full monthly salary for a full-month period, prorated for a shorter one.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" className={btnPrimary} disabled={saving}>
                {saving ? "Generating…" : "Generate"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
