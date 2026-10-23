import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

/**
 * Wraps a route element and redirects to the dashboard unless the current
 * user is Management or Admin -- for a page like the ticket KPI report
 * that the backend restricts via IsManagement (accounts.permissions)
 * rather than a Section a staff member's allowed_sections can grant.
 *
 * Same relationship to the backend as SectionRoute has to its own check:
 * this is not the actual access boundary (the API's IsManagement is), it
 * just keeps someone without access from landing on a page that would
 * only ever come back 403.
 */
export function ManagementRoute({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const allowed = user?.role === "management" || user?.role === "admin";
  if (!allowed) return <Navigate to="/admin" replace />;
  return <>{children}</>;
}
