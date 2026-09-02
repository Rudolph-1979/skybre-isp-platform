import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { useViewAs } from "../../context/ViewAsContext";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { PdfPreviewModal } from "../../components/PdfPreviewModal";
import type { Invoice, Payment } from "../../types";

// Customers see the same PDF staff do -- the endpoint lets the owning
// customer read their own documents (see _DocumentPdfMixin on the backend),
// so no second, customer-safe renderer is needed.
function docNounFor(status: Invoice["status"]) {
  return status === "quote" ? "quote" : status === "proforma" ? "pro forma" : "invoice";
}

export function PortalInvoices() {
  // The signed-in customer normally, or the customer a staff member is
  // viewing the portal as (see ViewAsContext).
  const { effectiveCustomerId: customerId } = useViewAs();
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [pdfFor, setPdfFor] = useState<Invoice | null>(null);
  const [showStatement, setShowStatement] = useState(false);

  useEffect(() => {
    if (!customerId) return;
    api.get<{ results: Invoice[] }>(`/invoices/?customer=${customerId}&ordering=-date_created`).then((res) => setInvoices(res.data.results));
    api.get<{ results: Payment[] }>(`/payments/?customer=${customerId}&ordering=-date`).then((res) => setPayments(res.data.results));
  }, [customerId]);

  return (
    <div>
      <PageHeader title="Invoices & Payments" subtitle="Your billing history." />

      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Invoices</h2>
        {customerId && (
          <button
            type="button"
            className="rounded-md border border-[var(--baseline)] px-3 py-1.5 text-sm font-medium text-[var(--text-primary)] hover:bg-[var(--tint-hover)]"
            onClick={() => setShowStatement(true)}
          >
            View statement
          </button>
        )}
      </div>
      <Table>
        <THead>
          <tr>
            <TH>Number</TH>
            <TH>Due date</TH>
            <TH>Total</TH>
            <TH>Balance due</TH>
            <TH>Status</TH>
            <TH>Document</TH>
          </tr>
        </THead>
        <tbody>
          {invoices.map((inv) => (
            <TR key={inv.id}>
              <TD className="font-medium">{inv.number}</TD>
              <TD>{inv.date_due}</TD>
              <TD className="tabular-nums">R {parseFloat(inv.total).toFixed(2)}</TD>
              <TD className="tabular-nums">R {parseFloat(inv.balance_due).toFixed(2)}</TD>
              <TD><StatusBadge status={inv.status} /></TD>
              <TD>
                <button
                  type="button"
                  className="text-sm font-medium text-[var(--series-1)] hover:underline"
                  onClick={() => setPdfFor(inv)}
                >
                  View PDF
                </button>
              </TD>
            </TR>
          ))}
          {invoices.length === 0 && <TR><TD className="text-[var(--text-muted)]">No invoices yet.</TD></TR>}
        </tbody>
      </Table>

      <h2 className="mb-2 mt-6 text-sm font-semibold">Payment history</h2>
      <Table>
        <THead>
          <tr>
            <TH>Date</TH>
            <TH>Amount</TH>
            <TH>Method</TH>
          </tr>
        </THead>
        <tbody>
          {payments.map((p) => (
            <TR key={p.id}>
              <TD>{new Date(p.date).toLocaleDateString()}</TD>
              <TD className="tabular-nums">R {parseFloat(p.amount).toFixed(2)}</TD>
              <TD className="capitalize">{p.method.replace("_", " ")}</TD>
            </TR>
          ))}
          {payments.length === 0 && <TR><TD className="text-[var(--text-muted)]">No payments yet.</TD></TR>}
        </tbody>
      </Table>

      {pdfFor && (
        <PdfPreviewModal
          title={`${pdfFor.number} — ${docNounFor(pdfFor.status)}`}
          url={`/invoices/${pdfFor.id}/pdf/`}
          filename={`${pdfFor.number}.pdf`}
          onClose={() => setPdfFor(null)}
        />
      )}

      {showStatement && customerId && (
        <PdfPreviewModal
          title="Your statement"
          url={`/customers/${customerId}/statement/pdf/`}
          filename="Statement.pdf"
          onClose={() => setShowStatement(false)}
        />
      )}
    </div>
  );
}
