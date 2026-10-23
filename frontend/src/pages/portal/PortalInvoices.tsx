import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { useViewAs } from "../../context/ViewAsContext";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { InvoiceStatusBadge } from "../../components/StatusBadge";
import { PdfPreviewModal } from "../../components/PdfPreviewModal";
import { formatIsoDate } from "../../utils/billingTerms";
import type { Invoice, Payment } from "../../types";

// Customers see the same PDF staff do -- the endpoint lets the owning
// customer read their own documents (see _DocumentPdfMixin on the backend),
// so no second, customer-safe renderer is needed.
//
// A credit note is an Invoice row with a negative total (no separate
// CreditNote model) and can carry any status word, so that's checked first.
function docNounFor(invoice: Pick<Invoice, "status" | "total">) {
  if (parseFloat(invoice.total) < 0) return "credit note";
  return invoice.status === "quote" ? "quote" : invoice.status === "proforma" ? "pro forma" : "invoice";
}

type Row =
  | { kind: "invoice"; date: string; invoice: Invoice }
  | { kind: "payment"; date: string; payment: Payment };

// "Manual" is how imported and hand-entered payments are stored; to a
// customer it is just a payment.
const METHOD_LABELS: Record<string, string> = {
  bank_transfer: "EFT",
  cash: "Cash",
  card: "Card",
  mobile_money: "Mobile money",
  manual: "Payment",
};

function rands(value: string) {
  return `R ${parseFloat(value).toFixed(2)}`;
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
    api.get<{ results: Invoice[] }>(`/invoices/?customer=${customerId}&ordering=-date_created&page_size=500`).then((res) => setInvoices(res.data.results));
    api.get<{ results: Payment[] }>(`/payments/?customer=${customerId}&ordering=-date&page_size=500`).then((res) => setPayments(res.data.results));
  }, [customerId]);

  // Newest first. On the same day a payment sits above the invoice, as it
  // happened after it.
  const rows: Row[] = [
    ...invoices.map((invoice) => ({ kind: "invoice" as const, date: invoice.date_issued || invoice.date_created, invoice })),
    ...payments.map((payment) => ({ kind: "payment" as const, date: payment.date.slice(0, 10), payment })),
  ].sort((a, b) => (a.date === b.date ? (a.kind === "payment" ? -1 : 1) : a.date < b.date ? 1 : -1));

  return (
    <div>
      <PageHeader title="Invoices & Payments" subtitle="Your billing history." />

      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Invoices and payments</h2>
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
      {/* One list, newest first (George, 12 Oct 2026: "Can we bring them
          into one?"). Invoices sit on the date they were issued, with the
          due date under it; payments on the day they were received. */}
      <Table>
        <THead>
          <tr>
            <TH>Date</TH>
            <TH>Description</TH>
            <TH>Amount</TH>
            <TH>Balance due</TH>
            <TH>Status</TH>
            <TH>Document</TH>
          </tr>
        </THead>
        <tbody>
          {rows.map((row) =>
            row.kind === "invoice" ? (
              <TR key={`i${row.invoice.id}`}>
                <TD className="whitespace-nowrap">
                  {formatIsoDate(row.date)}
                  <div className="text-xs text-[var(--text-muted)]">Due {formatIsoDate(row.invoice.date_due)}</div>
                </TD>
                <TD>
                  <span className="font-medium">{row.invoice.number}</span>
                  <div className="text-xs capitalize text-[var(--text-muted)]">{docNounFor(row.invoice)}</div>
                </TD>
                <TD className="tabular-nums">{rands(row.invoice.total)}</TD>
                <TD className="tabular-nums">{rands(row.invoice.balance_due)}</TD>
                <TD><InvoiceStatusBadge invoice={row.invoice} /></TD>
                <TD>
                  <button
                    type="button"
                    className="text-sm font-medium text-[var(--series-1)] hover:underline"
                    onClick={() => setPdfFor(row.invoice)}
                  >
                    View PDF
                  </button>
                </TD>
              </TR>
            ) : (
              <TR key={`p${row.payment.id}`} className="bg-[var(--tint-subtle)]">
                <TD className="whitespace-nowrap">{formatIsoDate(row.date)}</TD>
                <TD>
                  <span className="font-medium">Payment received</span>
                  <div className="text-xs text-[var(--text-muted)]">{METHOD_LABELS[row.payment.method] ?? "Payment"}</div>
                </TD>
                <TD className="tabular-nums">− {rands(row.payment.amount)}</TD>
                <TD className="text-[var(--text-muted)]">—</TD>
                <TD className="text-sm text-[var(--text-secondary)]">Received</TD>
                <TD className="text-[var(--text-muted)]">—</TD>
              </TR>
            )
          )}
          {rows.length === 0 && <TR><TD className="text-[var(--text-muted)]">Nothing yet.</TD></TR>}
        </tbody>
      </Table>

      {pdfFor && (
        <PdfPreviewModal
          title={`${pdfFor.number} — ${docNounFor(pdfFor)}`}
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
