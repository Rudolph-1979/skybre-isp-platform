import { useEffect, useState } from "react";
import { api } from "../api/client";
import { FormField, Modal, btnPrimary, btnSecondary, inputClass } from "./Modal";
import { apiErrorMessage } from "../utils/apiError";
import { formatIsoDate } from "../utils/billingTerms";
import { localIsoDate } from "../utils/quoteDefaults";
import type { Service } from "../types";

/**
 * Pause a service (#211, Rudolph 21 Oct 2026). While paused the line is OFF
 * (the login is refused) and it isn't billed for the paused days, pro rata.
 * Days already invoiced become a credit request for Management. The resume
 * date is optional: with one the line comes back by itself that morning,
 * without one it stays paused until somebody presses Resume.
 * Admin / Management only (see billing.service_pause).
 */

type Credit = {
  invoice_number: string;
  from: string;
  to: string;
  days: number;
  period_days: number;
  invoice_amount: string;
  amount: string;
};
type Plan = {
  pause_from: string;
  resume_on: string | null;
  last_day_on: string;
  starts_now: boolean;
  credits: Credit[];
  credit_total: string;
  unknown_invoices: string[];
  committed: boolean;
};
type ResumeResult = {
  withdrawn: boolean;
  back_on: string | null;
  line_status: string;
  credits: { id: number; amount: string; invoice_number: string | null }[];
  kept_credits: number[];
  unbilled: { from: string; to: string; days: number; period_days: number; amount_incl_vat: string } | null;
};

const rands = (v: string) =>
  `R ${Number(v).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/** "Paused until 05/11" (the day it comes back) or "Paused", for the badge. */
export function pauseLabel(service: Service): string | undefined {
  const p = service.pause;
  if (service.status === "paused") {
    return p?.resume_on ? `Paused until ${formatIsoDate(p.resume_on).slice(0, 5)}` : "Paused";
  }
  return undefined;
}

export function PauseServiceModal({
  service,
  onClose,
  onDone,
}: {
  service: Service;
  onClose: () => void;
  onDone: () => void;
}) {
  const [pauseFrom, setPauseFrom] = useState(localIsoDate());
  const [resumeOn, setResumeOn] = useState("");
  const [reason, setReason] = useState("");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const body = (commit: boolean) => ({
    pause_from: pauseFrom || null,
    resume_on: resumeOn || null,
    reason,
    ...(commit ? { commit: "1" } : {}),
  });

  useEffect(() => {
    const timer = window.setTimeout(() => {
      api
        .post<Plan>(`/services/${service.id}/pause/`, body(false))
        .then((res) => {
          setPlan(res.data);
          setError("");
        })
        .catch((err) => {
          setPlan(null);
          setError(apiErrorMessage(err, "Couldn't work it out."));
        });
    }, 250);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pauseFrom, resumeOn, service.id]);

  async function submit() {
    setBusy(true);
    setError("");
    try {
      await api.post(`/services/${service.id}/pause/`, body(true));
      onDone();
    } catch (err) {
      setError(apiErrorMessage(err, "Couldn't pause the service."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`Pause ${service.tariff_name}`} onClose={onClose}>
      {service.radius_username && (
        <p className="-mt-2 mb-3 font-mono text-xs text-[var(--text-muted)]">{service.radius_username}</p>
      )}
      <FormField label="Off from (first paused day)" required hint="Today, or a later date to book the pause.">
        <input type="date" className={inputClass} value={pauseFrom} min={localIsoDate()} onChange={(e) => setPauseFrom(e.target.value)} />
      </FormField>
      <FormField
        label="Back on (first day back)"
        hint="Optional. With a date the line switches back on by itself that morning; blank = stays paused until someone presses Resume."
      >
        <input type="date" className={inputClass} value={resumeOn} min={pauseFrom || undefined} onChange={(e) => setResumeOn(e.target.value)} />
      </FormField>
      <FormField label="Reason" required>
        <textarea className={inputClass} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
      </FormField>

      {plan && (
        <div className="mb-3 space-y-2 rounded-md border border-[var(--border-hairline)] bg-[var(--surface-2)] p-3 text-sm">
          <p>
            <span className="font-medium text-[var(--status-warning)]">Internet will be OFF while paused.</span>
            <span className="block text-xs text-[var(--text-muted)]">
              {plan.starts_now
                ? "Pauses now — the login is refused and the client is disconnected as soon as you confirm."
                : `Last day on: ${formatIsoDate(plan.last_day_on)}. Switched off at about 04:15 on ${formatIsoDate(plan.pause_from)}.`}
            </span>
            <span className="block text-xs text-[var(--text-muted)]">
              {plan.resume_on
                ? `Back on by itself on ${formatIsoDate(plan.resume_on)} (about 04:15).`
                : "Stays paused until someone presses Resume."}
            </span>
          </p>
          <p className="text-xs text-[var(--text-secondary)]">
            Billing: the paused days are not billed. The billing run charges a month the pause starts or ends in pro rata,
            and nothing for a month that is paused throughout.
          </p>
          {plan.credits.length > 0 && (
            <div>
              <p className="font-medium text-[var(--status-warning)]">
                Already invoiced — credit for Management to approve: {rands(plan.credit_total)}
              </p>
              <ul className="mt-1 space-y-0.5 text-xs text-[var(--text-secondary)] tabular-nums">
                {plan.credits.map((c) => (
                  <li key={c.invoice_number}>
                    {c.invoice_number} ({rands(c.invoice_amount)}): {c.days} of {c.period_days} days paused,{" "}
                    {formatIsoDate(c.from)} to {formatIsoDate(c.to)} → {rands(c.amount)}
                  </li>
                ))}
              </ul>
              {!plan.resume_on && (
                <p className="mt-1 text-xs text-[var(--text-muted)]">
                  Worked out to the end of the invoiced month. Resuming earlier recalculates it.
                </p>
              )}
            </div>
          )}
          {plan.unknown_invoices.length > 0 && (
            <p className="text-xs text-[var(--status-warning)]">
              Not worked out (no billing period on the invoice): {plan.unknown_invoices.join(", ")} — check by hand.
            </p>
          )}
        </div>
      )}

      {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}

      <div className="flex justify-end gap-2">
        <button type="button" className={btnSecondary} onClick={onClose}>
          Close
        </button>
        <button type="button" className={btnPrimary} disabled={busy || !plan || !reason.trim()} onClick={submit}>
          {plan?.starts_now ? "Pause now" : "Book pause"}
        </button>
      </div>
    </Modal>
  );
}

/**
 * Resume a paused service now, or cancel a pause booked for later. Asks
 * first, then reports what happened to the credit and any days to invoice
 * by hand. Returns true when something changed.
 */
export async function resumeService(service: Service): Promise<boolean> {
  const booked = service.pause?.status === "scheduled";
  const question = booked
    ? `Cancel the pause of ${service.tariff_name} booked from ${service.pause ? formatIsoDate(service.pause.pause_from) : ""}? A credit still awaiting approval is rejected.`
    : `Resume ${service.tariff_name} now? The line switches back on immediately.`;
  if (!window.confirm(question)) return false;
  try {
    const res = await api.post<ResumeResult>(`/services/${service.id}/resume/`);
    const r = res.data;
    const notes: string[] = [];
    if (r.withdrawn) notes.push("The booked pause was cancelled.");
    else notes.push(r.line_status === "suspended" ? "Resumed — but the client is off, so the line is Suspended with them." : "Resumed — the line is back on.");
    if (r.credits.length) {
      notes.push(
        `Credit recalculated for the days actually paused: ${r.credits
          .map((c) => `${rands(c.amount)}${c.invoice_number ? ` on ${c.invoice_number}` : ""}`)
          .join(", ")} (awaiting approval).`,
      );
    }
    if (r.kept_credits.length) notes.push("A credit already approved for this pause was left as it is — adjust by hand if needed.");
    if (r.unbilled) {
      notes.push(
        `Not billed: ${formatIsoDate(r.unbilled.from)} to ${formatIsoDate(r.unbilled.to)} (${r.unbilled.days} of ${r.unbilled.period_days} days, about ${rands(
          r.unbilled.amount_incl_vat,
        )} incl. VAT). Invoice it pro rata by hand if the client must pay for it (Finance → New invoice → Pro rata).`,
      );
    }
    window.alert(notes.join("\n\n"));
    return true;
  } catch (err) {
    window.alert(apiErrorMessage(err, "Couldn't resume the service."));
    return false;
  }
}

/** Under the tariff on the Services tab: the current or booked pause. */
export function ServicePauseNote({ service }: { service: Service }) {
  const p = service.pause;
  if (!p) return null;
  return (
    <span className="mt-0.5 block text-xs text-[var(--series-1)]" title={p.reason}>
      {p.status === "scheduled"
        ? `Pause booked from ${formatIsoDate(p.pause_from)}${p.resume_on ? `, back on ${formatIsoDate(p.resume_on)}` : " (until resumed)"}`
        : `Paused since ${formatIsoDate(p.pause_from)}${p.resume_on ? ` — back on ${formatIsoDate(p.resume_on)}` : " — until resumed"}`}
      {(p.credit_requests ?? []).map((r) => (
        <span key={r.id} className="block text-[var(--text-muted)]">
          Credit {rands(r.amount)}
          {r.invoice_number ? ` on ${r.invoice_number}` : ""} — {r.status === "pending" ? "awaiting approval" : r.status}
        </span>
      ))}
    </span>
  );
}
