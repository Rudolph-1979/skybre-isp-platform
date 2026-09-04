import { useEffect, useState, type FormEvent } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { api } from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { PdfPreviewModal } from "../../components/PdfPreviewModal";
import type { Invoice, Payment, InvoiceDeletionRequest } from "../../types";

export function InvoiceDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user: currentUser } = useAuth();
  const canDecideDeletion = currentUser?.role === "admin" || currentUser?.role === "management";
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState<Payment["method"]>("cash");
  const [saving, setSaving] = useState(false);
  const [converting, setConverting] = useState(false);
  const [showPdf, setShowPdf] = useState(false);
  const [convertError, setConvertError] = useState("");
  const [paymentError, setPaymentError] = useState("");

  // -- Deletion request (deleting a quote/pro forma needs Management
  // approval -- see InvoiceDeletionRequest on the backend) --
  const [deletionRequest, setDeletionRequest] = useState<InvoiceDeletionRequest | null>(null);
  const [showRequestDeletion, setShowRequestDeletion] = useState(false);
  const [deletionReason, setDeletionReason] = useState("");
  const [deletionSaving, setDeletionSaving] = useState(false);
  const [deletionError, setDeletionError] = useState<string | null>(null);
  const [rejectingDeletion, setRejectingDeletion] = useState(false);
  const [rejectDecisionNote, setRejectDecisionNote] = useState("");
  const [deletionBusy, setDeletionBusy] = useState(false);

  function refetchDeletionRequest() {
    if (!id) return;
    api
      .get<{ results: InvoiceDeletionRequest[] }>(`/invoice-deletion-requests/?invoice=${id}&status=pending`)
      .then((res) => setDeletionRequest(res.data.results[0] ?? null));
  }

  function load() {
    if (!id) return;
    api.get<Invoice>(`/invoices/${id}/`).then((res) => setInvoice(res.data));
    api.get<{ results: Payment[] }>(`/payments/?invoice=${id}`).then((res) => setPayments(res.data.results));
    refetchDeletionRequest();
  }

  useEffect(load, [id]);

  async function handleRequestDeletion(e: FormEvent) {
    e.preventDefault();
    if (!id) return;
    setDeletionSaving(true);
    setDeletionError(null);
    try {
      await api.post("/invoice-deletion-requests/", { invoice: Number(id), reason: deletionReason });
      setShowRequestDeletion(false);
      setDeletionReason("");
      refetchDeletionRequest();
    } catch (err) {
      const data = (err as { response?: { data?: unknown } })?.response?.data;
      const message = data && typeof data === "object" ? Object.values(data).flat().join(" ") : null;
      setDeletionError(message || "Couldn't submit this deletion request.");
    } finally {
      setDeletionSaving(false);
    }
  }

  async function handleApproveDeletion() {
    if (!deletionRequest || !invoice) return;
    if (
      !confirm(
        `Permanently delete ${invoice.number}? This can't be undone.`
      )
    )
      return;
    setDeletionBusy(true);
    try {
      await api.post(`/invoice-deletion-requests/${deletionRequest.id}/approve/`);
      navigate("/admin/finance");
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      alert(detail || "Couldn't approve this deletion request.");
      setDeletionBusy(false);
    }
  }

  async function handleRejectDeletion(e: FormEvent) {
    e.preventDefault();
    if (!deletionRequest) return;
    setDeletionBusy(true);
    try {
      await api.post(`/invoice-deletion-requests/${deletionRequest.id}/reject/`, { decision_note: rejectDecisionNote });
      setRejectingDeletion(false);
      setRejectDecisionNote("");
      refetchDeletionRequest();
    } finally {
      setDeletionBusy(false);
    }
  }

  async function handleWithdrawDeletion() {
    if (!deletionRequest) return;
    if (!confirm("Withdraw this deletion request?")) return;
    await api.delete(`/invoice-deletion-requests/${deletionRequest.id}/`);
    refetchDeletionRequest();
  }

  async function handleConvert(action: "convert-to-proforma" | "convert-to-invoice") {
    if (!id) return;
    setConverting(true);
    setConvertError("");
    try {
      const res = await api.post<Invoice>(`/invoices/${id}/${action}/`);
      setInvoice(res.data);
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const detail = data?.detail;
      setConvertError(typeof detail === "string" ? detail : "Could not convert this document — please try again.");
    } finally {
      setConverting(false);
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!invoice) return;
    setSaving(true);
    setPaymentError("");
    try {
      await api.post("/payments/", { customer: invoice.customer, invoice: invoice.id, amount, method });
      setShowModal(false);
      setAmount("");
      load();
    } catch (err) {
      // This used to be try/finally with no catch at all, so a rejected
      // payment closed nothing, said nothing, and left the ledger
      // untouched while the staff member had no reason to think it had
      // failed -- and the customer carried on being chased. The payment
      // serializer returns FIELD-KEYED errors ("that invoice belongs to a
      // different customer", "is still a draft and has not been issued"),
      // not just `detail`, so the message has to be dug out of either
      // shape.
      const data = (err as { response?: { data?: unknown } })?.response?.data;
      let message = "Could not record this payment — please try again.";
      if (data && typeof data === "object") {
        const detail = (data as { detail?: unknown }).detail;
        const first = Object.values(data as Record<string, unknown>).flat()[0];
        if (typeof detail === "string") message = detail;
        else if (typeof first === "string") message = first;
      }
      setPaymentError(message);
    } finally {
      setSaving(false);
    }
  }

  if (!invoice) return <p className="text-[var(--text-muted)]">Loading…</p>;

  // Wording follows the document's real state, same as the PDF's own heading
  // (the backend picks the template from invoice.status, not from a param).
  const docNoun =
    invoice.status === "quote" ? "quote" : invoice.status === "proforma" ? "pro forma" : "invoice";

  return (
    <div>
      <Link to="/admin/finance" className="mb-4 inline-block text-sm text-[var(--series-1)] hover:underline">
        ← Back to finance
      </Link>
      <PageHeader
        title={invoice.number}
        subtitle={`Billed to ${invoice.customer_name} · ${invoice.status === "quote" ? "Valid until" : "Due"} ${invoice.date_due}`}
        actions={
          <>
            <StatusBadge status={invoice.status} />
            <button type="button" className={btnSecondary} onClick={() => setShowPdf(true)}>
              Preview {docNoun}
            </button>
            {invoice.can_convert_to_proforma && (
              <button className={btnSecondary} disabled={converting} onClick={() => handleConvert("convert-to-proforma")}>
                {converting ? "Converting…" : "Convert to pro forma"}
              </button>
            )}
            {invoice.can_convert_to_invoice && (
              <button className={btnPrimary} disabled={converting} onClick={() => handleConvert("convert-to-invoice")}>
                {converting ? "Converting…" : "Convert to invoice"}
              </button>
            )}
            {invoice.status !== "quote" && invoice.status !== "proforma" && invoice.status !== "paid" && invoice.status !== "cancelled" && (
              <button className={btnPrimary} onClick={() => setShowModal(true)}>Record payment</button>
            )}
            {(invoice.status === "quote" || invoice.status === "proforma") && !deletionRequest && (
              <button
                type="button"
                className="text-sm font-medium text-red-600 hover:underline dark:text-red-400"
                onClick={() => {
                  setDeletionReason("");
                  setDeletionError(null);
                  setShowRequestDeletion(true);
                }}
              >
                Request deletion
              </button>
            )}
          </>
        }
      />

      {deletionRequest && (
        <div className="mb-6 rounded-lg border border-red-300 bg-red-50 p-4 text-sm dark:border-red-900 dark:bg-red-950">
          <p className="font-medium text-red-800 dark:text-red-200">
            Deletion requested by {deletionRequest.requested_by_name ?? "a staff member"} — awaiting Management
            approval.
          </p>
          <p className="mt-1 text-red-700 dark:text-red-300">Reason: {deletionRequest.reason}</p>
          <div className="mt-3 flex gap-2">
            {canDecideDeletion && (
              <>
                <button
                  type="button"
                  className={btnPrimary}
                  disabled={deletionBusy}
                  onClick={handleApproveDeletion}
                >
                  {deletionBusy ? "Deleting…" : "Approve & delete"}
                </button>
                <button
                  type="button"
                  className={btnSecondary}
                  disabled={deletionBusy}
                  onClick={() => {
                    setRejectingDeletion(true);
                    setRejectDecisionNote("");
                  }}
                >
                  Reject
                </button>
              </>
            )}
            <button type="button" className={btnSecondary} onClick={handleWithdrawDeletion}>
              Withdraw
            </button>
          </div>
        </div>
      )}

      {convertError && <p className="mb-4 text-sm text-[var(--status-critical)]">{convertError}</p>}

      {invoice.status === "proforma" && (
        <p className="mb-4 rounded-md border border-[var(--border-hairline)] bg-[var(--tint-subtle)] p-3 text-sm text-[var(--text-muted)]">
          This is a pro forma invoice — not a tax invoice or a demand for payment. Convert it to an invoice once it's approved/paid.
        </p>
      )}

      <div className="mb-6 grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4">
          <p className="text-[var(--text-muted)]">Subtotal</p>
          <p className="tabular-nums mt-1 text-lg font-semibold">R {parseFloat(invoice.subtotal).toFixed(2)}</p>
        </div>
        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4">
          <p className="text-[var(--text-muted)]">Tax</p>
          <p className="tabular-nums mt-1 text-lg font-semibold">R {parseFloat(invoice.tax_total).toFixed(2)}</p>
        </div>
        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4">
          <p className="text-[var(--text-muted)]">Total</p>
          <p className="tabular-nums mt-1 text-lg font-semibold">R {parseFloat(invoice.total).toFixed(2)}</p>
        </div>
        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4">
          <p className="text-[var(--text-muted)]">Balance due</p>
          <p className="tabular-nums mt-1 text-lg font-semibold">R {parseFloat(invoice.balance_due).toFixed(2)}</p>
        </div>
      </div>

      <h2 className="mb-2 text-sm font-semibold">Line items</h2>
      <Table>
        <THead>
          <tr>
            <TH>Description</TH>
            <TH>Period</TH>
            <TH>Qty</TH>
            <TH>Unit price</TH>
            <TH>Tax %</TH>
            <TH>Total</TH>
          </tr>
        </THead>
        <tbody>
          {invoice.items.map((item) => (
            <TR key={item.id}>
              <TD>
                {item.description}
                {item.item_type === "product" && (
                  <span className="ml-2 text-xs text-[var(--text-muted)]">(stock item)</span>
                )}
                {item.item_type === "tariff" && (
                  <span className="ml-2 text-xs text-[var(--text-muted)]">
                    (tariff plan{item.service ? " — service active" : ""})
                  </span>
                )}
              </TD>
              <TD className="text-[var(--text-muted)]">
                {item.period_start && item.period_end ? `${item.period_start} → ${item.period_end}` : "—"}
              </TD>
              <TD>{item.quantity}</TD>
              <TD className="tabular-nums">R {parseFloat(item.unit_price).toFixed(2)}</TD>
              <TD>{item.tax_rate_pct}%</TD>
              <TD className="tabular-nums">R {parseFloat(item.total).toFixed(2)}</TD>
            </TR>
          ))}
        </tbody>
      </Table>

      <h2 className="mb-2 mt-6 text-sm font-semibold">Payments</h2>
      <Table>
        <THead>
          <tr>
            <TH>Date</TH>
            <TH>Amount</TH>
            <TH>Method</TH>
            <TH>Received by</TH>
          </tr>
        </THead>
        <tbody>
          {payments.map((p) => (
            <TR key={p.id}>
              <TD>{new Date(p.date).toLocaleDateString()}</TD>
              <TD className="tabular-nums">R {parseFloat(p.amount).toFixed(2)}</TD>
              <TD className="capitalize">{p.method.replace("_", " ")}</TD>
              <TD>{p.received_by_name ?? "—"}</TD>
            </TR>
          ))}
          {payments.length === 0 && <TR><TD className="text-[var(--text-muted)]">No payments recorded yet.</TD></TR>}
        </tbody>
      </Table>

      {showModal && (
        <Modal title="Record payment" onClose={() => { setShowModal(false); setPaymentError(""); }}>
          <form onSubmit={handleSubmit}>
            {paymentError && (
              <p className="mb-3 rounded-md border border-[var(--status-critical)] bg-[var(--tint-subtle)] p-2 text-sm text-[var(--status-critical)]">
                {paymentError}
              </p>
            )}
            <FormField label="Amount (R)">
              <input type="number" step="0.01" required className={inputClass} value={amount} onChange={(e) => setAmount(e.target.value)} />
            </FormField>
            <FormField label="Method">
              <select className={inputClass} value={method} onChange={(e) => setMethod(e.target.value as Payment["method"])}>
                <option value="cash">Cash</option>
                <option value="card">Card</option>
                <option value="bank_transfer">Bank Transfer</option>
                <option value="mobile_money">Mobile Money</option>
                <option value="manual">Manual Adjustment</option>
              </select>
            </FormField>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => { setShowModal(false); setPaymentError(""); }}>Cancel</button>
              <button type="submit" disabled={saving} className={btnPrimary}>{saving ? "Saving…" : "Record payment"}</button>
            </div>
          </form>
        </Modal>
      )}

      {showRequestDeletion && (
        <Modal title="Request deletion" onClose={() => setShowRequestDeletion(false)}>
          <form onSubmit={handleRequestDeletion}>
            <p className="mb-3 text-sm text-[var(--text-secondary)]">
              Deleting {invoice.number} is irreversible, so a Management (or Admin) user must approve it before
              anything is actually deleted.
            </p>
            <FormField label="Reason">
              <textarea
                className={inputClass}
                rows={3}
                required
                value={deletionReason}
                onChange={(e) => setDeletionReason(e.target.value)}
                placeholder="e.g. Duplicate quote, customer declined, created in error…"
              />
            </FormField>
            {deletionError && <p className="mb-3 text-sm text-red-700 dark:text-red-300">{deletionError}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowRequestDeletion(false)}>Cancel</button>
              <button type="submit" disabled={deletionSaving} className={btnPrimary}>
                {deletionSaving ? "Submitting…" : "Submit request"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {rejectingDeletion && (
        <Modal title="Reject deletion request" onClose={() => setRejectingDeletion(false)}>
          <form onSubmit={handleRejectDeletion}>
            <p className="mb-3 text-sm text-[var(--text-secondary)]">
              Rejecting the deletion request for {invoice.number}. It'll remain as-is.
            </p>
            <FormField label="Note (optional)">
              <input
                className={inputClass}
                value={rejectDecisionNote}
                onChange={(e) => setRejectDecisionNote(e.target.value)}
              />
            </FormField>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setRejectingDeletion(false)}>Cancel</button>
              <button type="submit" className={btnPrimary} disabled={deletionBusy}>
                {deletionBusy ? "Rejecting…" : "Reject request"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {showPdf && (
        <PdfPreviewModal
          title={`${invoice.number} — ${docNoun}`}
          url={`/invoices/${invoice.id}/pdf/`}
          filename={`${invoice.number}.pdf`}
          onClose={() => setShowPdf(false)}
        />
      )}
    </div>
  );
}
