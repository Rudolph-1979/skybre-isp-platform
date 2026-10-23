/**
 * Reverse a payment -- the way out when money was confirmed against the
 * wrong customer (or recorded when it should not have been).
 *
 * Calls POST /payments/<id>/reverse/, which runs the same ledger reversal
 * as deleting the payment (Payment.reverse_ledger_effect): the customer's
 * balance goes back up by the amount, any invoices it settled go back to
 * owing, and a bank-feed transaction it came from returns to
 * "Matched (awaiting confirmation)" so it can be reassigned and confirmed
 * again. The reason is required and lands in the audit trail on the
 * customer's History tab.
 *
 * Accounts, Management and Admin only -- the backend enforces that; the
 * callers only hide the button from everyone else.
 */
import { useState } from "react";
import { api } from "../api/client";
import { Modal, FormField, inputClass, btnSecondary } from "./Modal";
import { apiErrorMessage } from "../utils/apiError";

const MIN_REASON = 5;

const btnDanger =
  "rounded-md bg-[var(--status-critical)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50";

interface ReverseResult {
  reversed: number;
  customer_name: string;
  amount: string;
  bank_transaction: number | null;
}

export function ReversePaymentModal({
  paymentId,
  customerName,
  amount,
  date,
  onInvoice,
  onClose,
  onReversed,
}: {
  paymentId: number;
  customerName: string;
  amount: string;
  date: string;
  /** Invoice number/id when the payment was recorded against one invoice. */
  onInvoice?: string | null;
  onClose: () => void;
  /** Called once the payment is gone, when the user closes the result. */
  onReversed: () => void;
}) {
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ReverseResult | null>(null);

  const rand = `R ${parseFloat(amount || "0").toFixed(2)}`;
  const reasonOk = reason.trim().length >= MIN_REASON;

  async function reverse() {
    setSaving(true);
    setError("");
    try {
      const res = await api.post<ReverseResult>(`/payments/${paymentId}/reverse/`, { reason: reason.trim() });
      setResult(res.data);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not reverse this payment — please try again."));
    } finally {
      setSaving(false);
    }
  }

  function finish() {
    onReversed();
    onClose();
  }

  if (result) {
    return (
      <Modal title="Payment reversed" onClose={finish}>
        <div className="space-y-3 text-sm">
          <p>
            {rand} has been taken off <strong>{result.customer_name || customerName}</strong>. Their balance is back
            to what it was before this payment, and any invoices it had settled are owing again.
          </p>
          {result.bank_transaction ? (
            <p>
              The bank transaction is back in <strong>Accountant → Bank Feeds → Matched (awaiting confirmation)</strong>,
              still showing {result.customer_name || customerName}. Pick the correct customer there, click
              <strong> Assign</strong>, then <strong>Confirm</strong>.
            </p>
          ) : (
            <p className="text-[var(--text-secondary)]">
              This payment was not from a bank feed, so there is nothing to reassign — record it again on the
              correct customer if the money belongs to someone else.
            </p>
          )}
          <div className="flex justify-end">
            <button type="button" className={btnSecondary} onClick={finish}>
              Close
            </button>
          </div>
        </div>
      </Modal>
    );
  }

  return (
    <Modal title="Reverse payment" onClose={onClose}>
      <div className="space-y-4 text-sm">
        <div className="rounded-md bg-[var(--tint-hover)] p-3">
          <div className="flex justify-between">
            <span className="text-[var(--text-muted)]">Customer</span>
            <span>{customerName}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-[var(--text-muted)]">Amount</span>
            <span className="tabular-nums">{rand}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-[var(--text-muted)]">Date</span>
            <span>{date}</span>
          </div>
          {onInvoice && (
            <div className="flex justify-between">
              <span className="text-[var(--text-muted)]">Recorded against invoice</span>
              <span>{onInvoice}</span>
            </div>
          )}
        </div>

        <div className="space-y-1 text-[var(--text-secondary)]">
          <p>Reversing will:</p>
          <ul className="ml-5 list-disc space-y-1">
            <li>put {rand} back on {customerName}'s balance, as if this payment had never been recorded;</li>
            <li>set any invoices it settled back to owing;</li>
            <li>send a bank-feed transaction back to the review queue, so it can go to the right customer.</li>
          </ul>
          <p className="pt-1">
            It can't be undone, and a "payment received" email already sent is not recalled. Reversing does
            not suspend anyone on the spot, but if {customerName} then qualifies for suspension (unpaid invoice
            past the blocking period, balance above the minimum), the daily 15:00 billing run will suspend them.
          </p>
        </div>

        <FormField label="Reason" required hint="Saved to the customer's History tab.">
          <textarea
            className={inputClass}
            rows={3}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. Confirmed to the wrong customer — belongs to …"
          />
        </FormField>

        {error && <p className="text-[var(--status-critical)]">{error}</p>}

        <div className="flex justify-end gap-2">
          <button type="button" className={btnSecondary} onClick={onClose}>
            Cancel
          </button>
          <button type="button" className={btnDanger} disabled={saving || !reasonOk} onClick={reverse}>
            {saving ? "Reversing…" : "Reverse payment"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
