/**
 * Pro rata for a part month (Rudolph, 7 Oct 2026): from the start date to
 * month end, inclusive, at monthly price x days used / days in the month.
 * Same rule as the server's sales.workflow.pro_rata (rounded half-up to
 * cents). Null when there's no date, or it starts on the 1st (a whole month).
 */
export type ProRata = { amount: number; days: number; monthDays: number; from: string; to: string };

export function proRata(startIso: string, monthly: number): ProRata | null {
  if (!startIso) return null;
  const [y, m, d] = startIso.split("-").map(Number);
  if (!y || !m || !d || d === 1) return null;
  const monthDays = new Date(y, m, 0).getDate();
  const days = monthDays - d + 1;
  const amount = Math.round(((monthly * days) / monthDays + Number.EPSILON) * 100) / 100;
  const to = `${y}-${String(m).padStart(2, "0")}-${String(monthDays).padStart(2, "0")}`;
  return { amount, days, monthDays, from: startIso, to };
}

/** "15/10/2026" from "2026-10-15". */
export function dmy(iso: string) {
  const [y, m, d] = iso.split("-");
  return `${d}/${m}/${y}`;
}
