/**
 * Default dates for the New quote / New invoice form (Finance -> Invoices).
 *
 * A quote used to default its "Valid until" to TODAY, so a quote nobody
 * edited expired the day it was raised (QUO-000001, 24 Sep 2026). Rudolph
 * chose 7 days on 2026-09-24. Staff can still change the date on the form.
 *
 * Dates are built from the browser's LOCAL calendar day. The old default
 * used toISOString(), which is the UTC day -- between 00:00 and 02:00 SAST
 * that is still yesterday.
 */
export const QUOTE_VALID_DAYS = 7;

const pad = (n: number) => String(n).padStart(2, "0");

/** "YYYY-MM-DD" for the local calendar day of `d`, plus `addDays`. */
export function localIsoDate(d: Date = new Date(), addDays = 0): string {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate() + addDays);
  return `${x.getFullYear()}-${pad(x.getMonth() + 1)}-${pad(x.getDate())}`;
}

/** Default "Valid until" for a new quote. */
export function defaultQuoteValidUntil(now: Date = new Date()): string {
  return localIsoDate(now, QUOTE_VALID_DAYS);
}

/** Default "Due date" for a new invoice -- today, unchanged behaviour. */
export function defaultInvoiceDueDate(now: Date = new Date()): string {
  return localIsoDate(now);
}
