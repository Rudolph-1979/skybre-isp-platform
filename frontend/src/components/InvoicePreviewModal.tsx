import { useEffect, useState } from "react";
import { api } from "../api/client";
import { FormField, inputClass } from "./Modal";
import { formatIsoDate } from "../utils/billingTerms";
import { PdfPreviewModal } from "./PdfPreviewModal";

/**
 * Preview invoice (George, 13 Oct 2026): the recurring invoice this client
 * would get on a date -- by default their next billing date -- worked out
 * by the same code the billing run uses, so discounts, "Rand amount up"
 * special pricing and recurring charges show exactly as they will bill.
 * Nothing is saved.
 */

interface PreviewLine {
  // Staff-only hint: the part of a plan line that is "Rand amount up".
  office_note?: string;
  description: string;
  quantity: string;
  unit_price: string;
  tax_rate_pct: string;
  total: string;
}

interface InvoicePreview {
  date: string;
  next_billing_date: string | null;
  billing_enabled: boolean;
  auto_create_invoices: boolean;
  doc_type: "invoice" | "proforma" | null;
  date_due: string | null;
  period_start?: string | null;
  period_end?: string | null;
  lines: PreviewLine[];
  subtotal: string;
  tax_total: string;
  total: string;
}

function rands(value: string | number) {
  return `R ${Number(value).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function InvoicePreviewModal({ customerId, onClose }: { customerId: number; onClose: () => void }) {
  const [date, setDate] = useState("");
  const [data, setData] = useState<InvoicePreview | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [showPdf, setShowPdf] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .get<InvoicePreview>(
        `/customer-billing-config/${customerId}/invoice-preview/${date ? `?date=${encodeURIComponent(date)}` : ""}`
      )
      .then((res) => {
        if (cancelled) return;
        setData(res.data);
        setError("");
        if (!date) setDate(res.data.date);
      })
      .catch(() => {
        if (!cancelled) setError("Couldn't load the invoice preview.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customerId, date]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay-scrim)] p-4" onClick={onClose}>
      <div
        className="max-h-[85vh] w-full max-w-3xl overflow-y-auto rounded-lg bg-[var(--surface-1)] p-4 shadow-lg sm:p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-[var(--text-primary)]">Pricing breakdown <span className="text-xs font-normal text-[var(--text-muted)]">office only</span></h2>
          <button onClick={onClose} className="text-[var(--text-muted)] hover:text-[var(--text-primary)]">
            ✕
          </button>
        </div>

        <div className="mb-3 flex flex-wrap items-end gap-3">
          <FormField label="Billing date">
            <input
              type="date"
              className={`${inputClass} w-auto`}
              value={date}
              onChange={(e) => e.target.value && setDate(e.target.value)}
            />
          </FormField>
          {data?.next_billing_date && date !== data.next_billing_date && (
            <button
              type="button"
              className="mb-2 text-xs text-[var(--series-1)] hover:underline"
              onClick={() => setDate(data.next_billing_date as string)}
            >
              Back to next billing date ({formatIsoDate(data.next_billing_date)})
            </button>
          )}
          {data && data.lines.length > 0 && (
            <button
              type="button"
              className="mb-2 rounded-md border border-[var(--border-hairline)] px-3 py-1.5 text-xs font-medium hover:bg-[var(--tint-subtle)]"
              onClick={() => setShowPdf(true)}
            >
              View PDF (as the client sees it)
            </button>
          )}
          {loading && data && <span className="mb-2 text-xs text-[var(--text-muted)]">Updating…</span>}
        </div>

        {error ? (
          <p className="text-sm text-[var(--status-critical)]">{error}</p>
        ) : loading && !data ? (
          <p className="text-sm text-[var(--text-muted)]">Loading…</p>
        ) : data && data.lines.length === 0 ? (
          <p className="rounded-md border border-[var(--border-hairline)] bg-[var(--surface-2)] px-3 py-4 text-sm text-[var(--text-secondary)]">
            Nothing would be billed on {formatIsoDate(data.date)} — no active service or recurring charge on that date.
          </p>
        ) : data ? (
          <>
            {(!data.billing_enabled || !data.auto_create_invoices) && (
              <p className="mb-3 rounded-md border border-[var(--status-warning)] px-3 py-2 text-xs text-[var(--status-warning)]">
                Recurring billing is switched off for this client, so the billing run won't raise this on its own.
              </p>
            )}
            <p className="mb-2 text-xs text-[var(--text-secondary)]">
              {data.doc_type === "proforma" ? "Pro forma" : "Invoice"} dated {formatIsoDate(data.date)}
              {data.date_due ? ` · due ${formatIsoDate(data.date_due)}` : ""}
              {data.period_start && data.period_end
                ? ` · period ${formatIsoDate(data.period_start)} – ${formatIsoDate(data.period_end)}`
                : ""}
            </p>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--border-hairline)] text-left text-xs uppercase text-[var(--text-muted)]">
                  <th className="py-2 pr-3 font-medium">Description</th>
                  <th className="py-2 pr-3 text-right font-medium">Qty</th>
                  <th className="py-2 pr-3 text-right font-medium">Unit excl. VAT</th>
                  <th className="py-2 pr-3 text-right font-medium">VAT</th>
                  <th className="py-2 text-right font-medium">Total incl. VAT</th>
                </tr>
              </thead>
              <tbody>
                {data.lines.map((l, i) => (
                  <tr key={i} className="border-b border-[var(--border-hairline)]">
                    <td className={`py-2 pr-3 ${Number(l.unit_price) < 0 ? "text-[var(--status-warning)]" : ""}`}>
                      {l.description}
                      {l.office_note && (
                        <span className="block text-xs text-[var(--series-1)]">{l.office_note}</span>
                      )}
                    </td>
                    <td className="py-2 pr-3 text-right tabular-nums">{Number(l.quantity)}</td>
                    <td className="py-2 pr-3 text-right tabular-nums">{rands(l.unit_price)}</td>
                    <td className="py-2 pr-3 text-right tabular-nums">{Number(l.tax_rate_pct)}%</td>
                    <td className="py-2 text-right tabular-nums">{rands(l.total)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={4} className="pt-3 pr-3 text-right text-[var(--text-secondary)]">Subtotal excl. VAT</td>
                  <td className="pt-3 text-right tabular-nums">{rands(data.subtotal)}</td>
                </tr>
                <tr>
                  <td colSpan={4} className="pr-3 text-right text-[var(--text-secondary)]">VAT</td>
                  <td className="text-right tabular-nums">{rands(data.tax_total)}</td>
                </tr>
                <tr className="font-semibold">
                  <td colSpan={4} className="pt-1 pr-3 text-right">Total incl. VAT</td>
                  <td className="pt-1 text-right tabular-nums">{rands(data.total)}</td>
                </tr>
              </tfoot>
            </table>
            <p className="mt-3 text-xs text-[var(--text-muted)]">
              Worked out by the same code as the billing run — nothing is saved. Once-off items, pro rata and
              any balance or credit on the account aren't included. Blue notes are for the office only — the client's
              invoice shows just the full line price.
            </p>
          </>
        ) : null}
      </div>
      {showPdf && data && (
        <div onClick={(e) => e.stopPropagation()}>
        <PdfPreviewModal
          title="Preview invoice — as the client will see it"
          url={`/customer-billing-config/${customerId}/invoice-preview/pdf/?date=${encodeURIComponent(data.date)}`}
          filename={`invoice-preview-${data.date}.pdf`}
          onClose={() => setShowPdf(false)}
        />
        </div>
      )}
    </div>
  );
}
