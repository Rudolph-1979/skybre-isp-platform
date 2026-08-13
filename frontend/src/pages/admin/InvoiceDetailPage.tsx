import { useEffect, useState, type FormEvent } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../../api/client";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import type { Invoice, Payment } from "../../types";

export function InvoiceDetailPage() {
  const { id } = useParams();
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState<Payment["method"]>("cash");
  const [saving, setSaving] = useState(false);

  function load() {
    if (!id) return;
    api.get<Invoice>(`/invoices/${id}/`).then((res) => setInvoice(res.data));
    api.get<{ results: Payment[] }>(`/payments/?invoice=${id}`).then((res) => setPayments(res.data.results));
  }

  useEffect(load, [id]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!invoice) return;
    setSaving(true);
    try {
      await api.post("/payments/", { customer: invoice.customer, invoice: invoice.id, amount, method });
      setShowModal(false);
      setAmount("");
      load();
    } finally {
      setSaving(false);
    }
  }

  if (!invoice) return <p className="text-[var(--text-muted)]">Loading…</p>;

  return (
    <div>
      <Link to="/admin/invoices" className="mb-4 inline-block text-sm text-[var(--series-1)] hover:underline">
        ← Back to invoices
      </Link>
      <PageHeader
        title={invoice.number}
        subtitle={`Billed to ${invoice.customer_name} · Due ${invoice.date_due}`}
        actions={
          <>
            <StatusBadge status={invoice.status} />
            {invoice.status !== "paid" && invoice.status !== "cancelled" && (
              <button className={btnPrimary} onClick={() => setShowModal(true)}>Record payment</button>
            )}
          </>
        }
      />

      <div className="mb-6 grid grid-cols-4 gap-4 text-sm">
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
            <TH>Qty</TH>
            <TH>Unit price</TH>
            <TH>Tax %</TH>
            <TH>Total</TH>
          </tr>
        </THead>
        <tbody>
          {invoice.items.map((item) => (
            <TR key={item.id}>
              <TD>{item.description}</TD>
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
        <Modal title="Record payment" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
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
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>Cancel</button>
              <button type="submit" disabled={saving} className={btnPrimary}>{saving ? "Saving…" : "Record payment"}</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
