import type { Section, User } from "../types";

/**
 * Whether `user` can see/use the given section (one of the sidebar tabs
 * listed in types/index.ts's Section type). Mirrors the backend's
 * accounts.permissions.user_can_access_section exactly:
 *  - Admin always has full access, regardless of allowed_sections.
 *  - An empty allowed_sections list means NO sections. It used to mean
 *    unrestricted, which made the whole mechanism opt-in: every account
 *    nobody had deliberately narrowed saw everything, including payroll
 *    and the credential store. Existing accounts were given explicit
 *    lists in the same release that changed this.
 *  - Non-staff (customer) accounts don't use this at all -- they're
 *    routed through the separate /portal layout, not /admin.
 *
 * This has to keep mirroring the backend's
 * accounts.permissions.user_can_access_section exactly. It is not a
 * security boundary -- the API is -- but when the two disagree the
 * sidebar offers a section the server then refuses, which reads as the
 * platform being broken rather than as a permission being absent.
 */
export function canAccessSection(user: User | null | undefined, section: Section): boolean {
  if (!user) return false;
  if (user.role === "admin") return true;
  return (user.allowed_sections ?? []).includes(section);
}
