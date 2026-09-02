import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { canAccessSection } from "../utils/permissions";
import type { Section } from "../types";

/**
 * Wraps a section's route element and redirects to the dashboard if the
 * current user doesn't have that section (see utils/permissions.ts).
 * This is the actual access-control boundary for direct URL navigation --
 * AdminLayout's nav filtering only hides the link, it doesn't stop
 * someone typing/bookmarking the URL directly.
 */
export function SectionRoute({ section, children }: { section: Section; children: ReactNode }) {
  const { user } = useAuth();
  if (!canAccessSection(user, section)) {
    return <Navigate to="/admin" replace />;
  }
  return <>{children}</>;
}
