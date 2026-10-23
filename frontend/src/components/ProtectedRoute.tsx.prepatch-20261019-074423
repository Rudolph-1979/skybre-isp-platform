import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useViewAs } from "../context/ViewAsContext";

export function ProtectedRoute({ children, staffOnly = false }: { children: ReactNode; staffOnly?: boolean }) {
  const { user, loading } = useAuth();
  const { target: viewingAs } = useViewAs();

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center text-[var(--text-muted)]">Loading…</div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;

  const isStaff = user.role !== "customer";
  if (staffOnly && !isStaff) return <Navigate to="/portal" replace />;
  // Staff are normally bounced out of the customer portal -- it isn't theirs
  // and their own customer_id is undefined, so the pages would render empty.
  // "View customer portal" is the one deliberate exception: while a view-as
  // target is set they are meant to be in there, looking at what the
  // customer sees. Without this the button navigated to /portal and this
  // guard immediately sent them back to the admin dashboard.
  if (!staffOnly && isStaff && !viewingAs) return <Navigate to="/admin" replace />;

  return <>{children}</>;
}
