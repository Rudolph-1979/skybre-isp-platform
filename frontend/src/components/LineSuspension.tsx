/**
 * Showing a suspended LINE where only the customer's status used to be shown.
 *
 * When billing suspends a customer for non-payment it suspends their
 * SERVICES (billing.recurring._process_blocking) and leaves Customer.status
 * as it was -- deliberately: Customer.status drives its own cascade onto
 * the services (customers.signals), and the proven suspend/reconnect cycle
 * depends on that staying untouched. So a customer whose only line is off
 * still reads "Active". Rather than change the status, these show the fact
 * next to it. Decided with Rudolph, 2026-09-23: show it, don't change it.
 */
import type { Service } from "../types";

/** The pill's two numbers worked out from a list of services -- for the
 *  customer page, which already holds that customer's services and so stays
 *  right when a line is suspended or restored without reloading the
 *  customer. */
export function countSuspended(services: Pick<Service, "status" | "suspended_for_nonpayment">[]) {
  const off = services.filter((s) => s.status === "suspended");
  return { suspended: off.length, nonpayment: off.filter((s) => s.suspended_for_nonpayment).length };
}

/** "1 line suspended — non-payment", or nothing when no line is suspended.
 *  `suspended`/`nonpayment` come from the customer API (null = not provided
 *  on this response, treated as nothing to show). */
export function LineSuspensionPill({
  suspended,
  nonpayment,
  compact = false,
}: {
  suspended: number | null | undefined;
  nonpayment: number | null | undefined;
  compact?: boolean;
}) {
  const n = suspended ?? 0;
  if (n <= 0) return null;
  const np = nonpayment ?? 0;
  const lines = `${n} line${n === 1 ? "" : "s"} suspended`;
  const why = np === 0 ? "" : np === n ? " — non-payment" : ` (${np} for non-payment)`;
  const title =
    np > 0
      ? "Suspended by the billing run for non-payment. It reconnects by itself once a payment brings the balance down to the minimum balance or below."
      : "Suspended by staff (or with the customer). A payment does not reconnect this line by itself.";
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-[#fdece9] px-2 py-0.5 text-xs font-medium text-[#c1512f] ${
        compact ? "mt-1" : ""
      }`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-[var(--status-serious)]" aria-hidden="true" />
      {lines}
      {why}
    </span>
  );
}

/** Small reason under a service's own Suspended badge. */
export function ServiceSuspensionReason({
  status,
  suspendedForNonpayment,
}: {
  status: string;
  suspendedForNonpayment: boolean | undefined;
}) {
  if (status !== "suspended" || !suspendedForNonpayment) return null;
  return (
    <span
      className="mt-0.5 block text-xs text-[var(--text-muted)]"
      title="Suspended by the billing run. It reconnects by itself once a payment brings the balance down to the minimum balance or below."
    >
      non-payment
    </span>
  );
}

/** Small note under a service's own status badge when it has a booked end
 *  date (package #21, 2026-09-24: "Also add a end date status"). The badge
 *  itself still says Active/Pending/etc -- this is the same "show it, don't
 *  change it" rule as ServiceSuspensionReason above, so the STATUS column
 *  alone tells you the line is on its way out, not only the Tariff column's
 *  own "Ends ..." note. `formatDate` is passed in (formatIsoDate) so this
 *  file does not need to import from utils/billingTerms itself. */
export function ServiceEndDateNote({
  status,
  endDate,
  formatDate,
}: {
  status: string;
  endDate: string | null | undefined;
  formatDate: (d: string) => string;
}) {
  if (!endDate || status === "terminated") return null;
  return (
    <span
      className="mt-0.5 block text-xs text-[var(--status-warning)]"
      title="The first day without service: the line is cut at 00:00 that day."
    >
      Ends {formatDate(endDate)}
    </span>
  );
}
