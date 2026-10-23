import { useEffect, useState } from "react";
import { api } from "../api/client";
import { FormField, Modal, btnPrimary, btnSecondary, inputClass } from "./Modal";
import { apiErrorMessage } from "../utils/apiError";
import { formatIsoDate } from "../utils/billingTerms";
import { localIsoDate } from "../utils/quoteDefaults";
import type { Service } from "../types";

/**
 * Cancel a service (#203, Rudolph 20 Oct 2026). One calendar month's notice,
 * unless the client can't be serviced on this line any more and moves to a
 * new service of theirs. Shows the end date and the money before anything is
 * saved: days already invoiced after the end date become a credit request
 * for Management; a last period not invoiced yet is billed pro rata by the
 * billing run. Admin / Management only (see billing.service_cancel).
 */

type Kind = "notice" | "cannot_service";
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
  kind: Kind;
  notice_date: string | null;
  minimum_end_date: string | null;
  end_date: string;
  last_day: string;
  ends_now: boolean;
  replacement_name: string;
  credits: Credit[];
  credit_total: string;
  unknown_invoices: string[];
  last_period: { from: string; to: string; days: number; period_days: number } | null;
  committed: boolean;
};

const rands = (v: string) =>
  `R ${Number(v).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export function CancelServiceModal({
  service,
  services,
  onClose,
  onDone,
}: {
  service: Service;
  services: Service[];
  onClose: () => void;
  onDone: () => void;
}) {
  const others = services.filter((s) => s.id !== service.id && s.status !== "terminated");
  const [kind, setKind] = useState<Kind>("notice");
  const [noticeDate, setNoticeDate] = useState(localIsoDate());
  const [endDate, setEndDate] = useState("");
  const [replacement, setReplacement] = useState<string>(others.length === 1 ? String(others[0].id) : "");
  const [reason, setReason] = useState("");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const body = (commit: boolean) => ({
    kind,
    notice_date: kind === "notice" ? noticeDate : null,
    end_date: endDate || null,
    replacement_service: kind === "cannot_service" ? replacement || null : null,
    reason,
    ...(commit ? { commit: "1" } : {}),
  });

  useEffect(() => {
    if (kind === "cannot_service" && !replacement) {
      setPlan(null);
      setError(others.length ? "" : "This client has no other service. Add the new service first.");
      return;
    }
    const timer = window.setTimeout(() => {
      api
        .post<Plan>(`/services/${service.id}/cancel/`, body(false))
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
  }, [kind, noticeDate, endDate, replacement, service.id]);

  async function submit() {
    setBusy(true);
    setError("");
    try {
      await api.post(`/services/${service.id}/cancel/`, body(true));
      onDone();
    } catch (err) {
      setError(apiErrorMessage(err, "Couldn't cancel the service."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`Cancel ${service.tariff_name}`} onClose={onClose}>
      {service.radius_username && (
        <p className="-mt-2 mb-3 font-mono text-xs text-[var(--text-muted)]">{service.radius_username}</p>
      )}
      <div className="mb-3 space-y-2 text-sm">
        <label className="flex items-start gap-2">
          <input type="radio" className="mt-1" checked={kind === "notice"} onChange={() => { setKind("notice"); setEndDate(""); }} />
          <span>
            <span className="font-medium">Client gave notice</span>
            <span className="block text-xs text-[var(--text-muted)]">One calendar month's notice is required.</span>
          </span>
        </label>
        <label className="flex items-start gap-2">
          <input
            type="radio"
            className="mt-1"
            checked={kind === "cannot_service"}
            onChange={() => { setKind("cannot_service"); setEndDate(""); }}
          />
          <span>
            <span className="font-medium">Can&rsquo;t be serviced any more &mdash; moving to a new service</span>
            <span className="block text-xs text-[var(--text-muted)]">No notice. Choose the client&rsquo;s new service.</span>
          </span>
        </label>
      </div>

      {kind === "notice" ? (
        <>
          <FormField label="Notice given on" required>
            <input type="date" className={inputClass} value={noticeDate} max={localIsoDate()} onChange={(e) => setNoticeDate(e.target.value)} />
          </FormField>
          <FormField label="Off from (first day without service)" hint="Blank = one calendar month after the notice date. Can be later, not earlier.">
            <input
              type="date"
              className={inputClass}
              value={endDate}
              min={plan?.minimum_end_date ?? undefined}
              onChange={(e) => setEndDate(e.target.value)}
            />
          </FormField>
        </>
      ) : (
        <>
          <FormField label="Moving to" required>
            <select className={inputClass} value={replacement} onChange={(e) => setReplacement(e.target.value)}>
              <option value="">Choose the new service…</option>
              {others.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.tariff_name}
                  {s.radius_username ? ` — ${s.radius_username}` : ""}
                  {s.start_date ? ` (from ${formatIsoDate(s.start_date)})` : ""}
                </option>
              ))}
            </select>
          </FormField>
          <FormField label="Off from (first day without service)" hint="Blank = the day the new service started, or today.">
            <input type="date" className={inputClass} value={endDate} onChange={(e) => setEndDate(e.target.value)} />
          </FormField>
        </>
      )}

      <FormField label="Reason" required>
        <textarea className={inputClass} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
      </FormField>

      {plan && (
        <div className="mb-3 space-y-2 rounded-md border border-[var(--border-hairline)] bg-[var(--surface-2)] p-3 text-sm">
          <p>
            <span className="font-medium">Last day of service: {formatIsoDate(plan.last_day)}</span>
            <span className="block text-xs text-[var(--text-muted)]">
              {plan.ends_now
                ? "Ends now — the line is cut as soon as you confirm."
                : `Cut at 00:00 on ${formatIsoDate(plan.end_date)}.`}
            </span>
          </p>
          {plan.last_period && (
            <p className="text-xs text-[var(--text-secondary)]">
              Not invoiced yet: the billing run charges {formatIsoDate(plan.last_period.from)} to{" "}
              {formatIsoDate(plan.last_period.to)} pro rata ({plan.last_period.days} of {plan.last_period.period_days} days).
            </p>
          )}
          {plan.credits.length > 0 ? (
            <div>
              <p className="font-medium text-[var(--status-warning)]">
                Pro rata credit for Management to approve: {rands(plan.credit_total)}
              </p>
              <ul className="mt-1 space-y-0.5 text-xs text-[var(--text-secondary)] tabular-nums">
                {plan.credits.map((c) => (
                  <li key={c.invoice_number}>
                    {c.invoice_number} ({rands(c.invoice_amount)}): {c.days} of {c.period_days} days unused,{" "}
                    {formatIsoDate(c.from)} to {formatIsoDate(c.to)} → {rands(c.amount)}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            !plan.last_period && <p className="text-xs text-[var(--text-secondary)]">No credit needed.</p>
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
          {plan?.ends_now ? "Cancel service now" : "Book cancellation"}
        </button>
      </div>
    </Modal>
  );
}

/** Under the tariff on the Services tab: the booked cancellation. */
export function ServiceCancellationNote({ service }: { service: Service }) {
  const c = service.cancellation;
  if (!c || service.status === "terminated") return null;
  const lastDay = new Date(`${c.end_date}T00:00:00`);
  lastDay.setDate(lastDay.getDate() - 1);
  const last = localIsoDate(lastDay);
  return (
    <span className="mt-0.5 block text-xs text-[var(--status-warning)]" title={c.reason}>
      {c.kind === "notice"
        ? `Cancelling — notice ${c.notice_date ? formatIsoDate(c.notice_date) : ""}, last day ${formatIsoDate(last)}`
        : `Moving to ${c.replacement_name || "a new service"} — last day ${formatIsoDate(last)}`}
      {c.credit_requests.map((r) => (
        <span key={r.id} className="block text-[var(--text-muted)]">
          Credit {rands(r.amount)}
          {r.invoice_number ? ` on ${r.invoice_number}` : ""} — {r.status === "pending" ? "awaiting approval" : r.status}
        </span>
      ))}
    </span>
  );
}
