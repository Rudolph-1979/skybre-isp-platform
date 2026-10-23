import type { Job } from "../types";

/** Who is on this job, as it should read on a calendar chip.
 *
 * "Unassigned" rather than an empty string when nobody is on it. A job
 * with no tech and a job whose tech simply wasn't displayed looked
 * identical on the month grid, and the first of those is the one worth
 * spotting on a Friday afternoon without opening anything.
 *
 * Comma-joined for two or more (Rudolph, 29 Sep: "allow me to assign to
 * two technicians"). Each name comes from the API (`technicians[].name`),
 * already in a stable order -- see scheduling/serializers.staff_display_name
 * and JobSerializer.get_technicians. Deciding that here instead would put
 * the rule in two places, and the month grid and the day grid would
 * eventually disagree about what to call the same person.
 */
export function jobAssignee(job: Pick<Job, "technicians">): string {
  const names = job.technicians.map((t) => t.name?.trim()).filter((n): n is string => !!n);
  return names.length > 0 ? names.join(", ") : "Unassigned";
}

/** One combined field_status for the calendar chip's dot/colour logic,
 * which only ever wants a single "how's it going" signal, not a list.
 *
 * Picks the least-finished technician's status (not_dispatched < pending <
 * en_route < checked_in < completed, cancelled treated like not_dispatched
 * for this purpose) so a two-person job doesn't look further along than
 * its slowest half -- the one still worth watching is the one that should
 * decide the chip's colour. An unassigned job (nobody to report anything)
 * reads not_dispatched, same as before this feature.
 */
const FIELD_STATUS_RANK: Record<string, number> = {
  not_dispatched: 0,
  cancelled: 0,
  pending: 1,
  en_route: 2,
  checked_in: 3,
  completed: 4,
};

export function jobFieldStatus(job: Pick<Job, "technicians">): string {
  if (job.technicians.length === 0) return "not_dispatched";
  return job.technicians
    .map((t) => t.field_status)
    .reduce((worst, s) => ((FIELD_STATUS_RANK[s] ?? 1) < (FIELD_STATUS_RANK[worst] ?? 1) ? s : worst));
}


// Staff pickers show first name AND surname (George, 14 Oct 2026: "I need
// surnames as well") -- two Jacos are otherwise indistinguishable. Falls
// back to the username for an account with no name on it.
export function staffFullName(u: { first_name?: string | null; last_name?: string | null; username?: string | null }) {
  const name = `${(u.first_name || "").trim()} ${(u.last_name || "").trim()}`.trim();
  return name || u.username || "";
}

export function byStaffName<T extends { first_name?: string | null; last_name?: string | null; username?: string | null }>(a: T, b: T) {
  return staffFullName(a).localeCompare(staffFullName(b), undefined, { sensitivity: "base" });
}
