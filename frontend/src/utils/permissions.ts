import type { Section, User } from "../types";

/**
 * Whether `user` can see/use the given section (one of the sidebar tabs
 * listed in types/index.ts's Section type). Mirrors the backend's
 * accounts.permissions.user_can_access_section exactly:
 *  - Admin always has full access, regardless of allowed_sections.
 *  - An empty allowed_sections list means unrestricted (the default for
 *    every account until an admin deliberately narrows it via
 *    Configs → Permissions).
 *  - Non-staff (customer) accounts don't use this at all -- they're
 *    routed through the separate /portal layout, not /admin.
 */
export function canAccessSection(user: User | null | undefined, section: Section): boolean {
  if (!user) return false;
  if (user.role === "admin") return true;
  const allowed = user.allowed_sections ?? [];
  if (allowed.length === 0) return true;
  return allowed.includes(section);
}
