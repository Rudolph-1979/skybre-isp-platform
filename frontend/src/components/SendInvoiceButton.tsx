import { useState } from "react";
import { api } from "../api/client";
import { Modal, btnPrimary, btnSecondary } from "./Modal";
import { apiErrorMessage } from "../utils/apiError";
import { formatIsoDateTime } from "../utils/billingTerms";
import type { Invoice } from "../types";

/**
 * Send / Resend an invoice, quote or pro forma to the client (Rudolph,
 * 7 Oct 2026: "Nowhere is there an option to send or resend invoices").
 * Same email and PDF the billing run sends: to the billing email, main email
 * in CC. Asks first, because it lands in a real inbox.
 */
export function SendInvoiceButton({
  invoice,
  variant = "link",
  onSent,
}: {
  invoice: Pick<Invoice, "id" | "number" | "status" | "customer_name" | "last_emailed_at" | "last_emailed_to">;
  variant?: "link" | "button";
  onSent?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  if (invoice.status === "draft" || invoice.status === "cancelled") return null;
  const sentBefore = !!invoice.last_emailed_at;
  const noun = invoice.status === "quote" ? "quote" : invoice.status === "proforma" ? "pro forma" : "invoice";
  const label = sentBefore ? "Resend" : "Send";

  async function send() {
    setBusy(true);
    setError("");
    try {
      const res = await api.post<{ sent_to: string; cc: string }>(`/invoices/${invoice.id}/send/`, {});
      setDone(`Sent to ${res.data.sent_to}${res.data.cc ? `, copy to ${res.data.cc}` : ""}.`);
      onSent?.();
    } catch (err) {
      setError(apiErrorMessage(err, "The email didn't go."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className={
          variant === "button"
            ? btnSecondary
            : "text-sm font-medium text-[var(--series-1)] hover:underline"
        }
        title={sentBefore ? `Last sent ${formatIsoDateTime(invoice.last_emailed_at!)} to ${invoice.last_emailed_to}` : `Email this ${noun} to the client`}
        onClick={(e) => {
          e.stopPropagation();
          setError("");
          setDone("");
          setOpen(true);
        }}
      >
        {variant === "button" ? `${label} ${noun}` : label}
      </button>
      {open && (
        <Modal title={`${label} ${invoice.number}`} onClose={() => setOpen(false)}>
          <div className="space-y-3 text-sm" onClick={(e) => e.stopPropagation()}>
            {done ? (
              <p className="text-[var(--text-secondary)]">{done}</p>
            ) : (
              <>
                <p className="text-[var(--text-secondary)]">
                  Email {noun} <strong>{invoice.number}</strong> with its PDF to {invoice.customer_name} — their
                  billing email, with their main email in copy.
                </p>
                {sentBefore && (
                  <p className="text-xs text-[var(--text-muted)]">
                    Last sent {formatIsoDateTime(invoice.last_emailed_at!)} to {invoice.last_emailed_to}.
                  </p>
                )}
              </>
            )}
            {error && <p className="text-[var(--status-critical)]">{error}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setOpen(false)}>
                {done ? "Close" : "Cancel"}
              </button>
              {!done && (
                <button type="button" className={btnPrimary} disabled={busy} onClick={send}>
                  {busy ? "Sending…" : label}
                </button>
              )}
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
