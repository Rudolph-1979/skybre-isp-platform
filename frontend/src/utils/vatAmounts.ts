// Rand discounts / "Rand amount up" are typed incl. VAT (George, 13 Oct 2026)
// but stored and billed excl. VAT, the way the tariff price is. Cents,
// ROUND_HALF_UP like the backend. Shared by the client's Services table and
// the Services page so both take the same figure (20 Oct 2026).
export function inclFromExclCents(exclCents: number, ratePct: number) {
  return Math.floor((exclCents * (100 + ratePct)) / 100 + 0.5 + 1e-9);
}
export function inclVatText(excl: string | number, ratePct: number) {
  const cents = Math.round(Number(excl) * 100);
  return (inclFromExclCents(cents, ratePct) / 100).toFixed(2);
}
// The excl. VAT amount (to the cent) whose incl. VAT figure is what was
// typed. Some incl. amounts can't be hit exactly at 15%; then the nearest.
export function exclFromIncl(incl: string | number, ratePct: number) {
  const target = Math.round(Number(incl) * 100);
  const base = Math.round((target * 100) / (100 + ratePct));
  let best = base;
  for (const c of [base, base - 1, base + 1]) {
    if (inclFromExclCents(c, ratePct) === target) return c / 100;
    if (Math.abs(inclFromExclCents(c, ratePct) - target) < Math.abs(inclFromExclCents(best, ratePct) - target)) best = c;
  }
  return best / 100;
}
