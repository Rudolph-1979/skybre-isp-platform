import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export function ProtectedRoute({ children, staffOnly = false }: { children: ReactNode; staffOnly?: boolean }) {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center text-[var(--text-muted)]">Loading…</div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;

  const isStaff = ["admin", "staff", "technician"].includes(user.role);
  if (staffOnly && !isStaff) return <Navigate to="/portal" replace />;
  if (!staffOnly && isStaff) return <Navigate to="/admin" replace />;

  return <>{children}</>;
}
