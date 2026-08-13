import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

const NAV = [
  { to: "/portal", label: "Overview", end: true },
  { to: "/portal/invoices", label: "Invoices & Payments" },
  { to: "/portal/tickets", label: "Support Tickets" },
];

export function PortalLayout() {
  const { user, logout } = useAuth();

  return (
    <div className="min-h-screen bg-[var(--page-plane)]">
      <header className="flex items-center justify-between border-b border-[var(--border-hairline)] bg-[var(--surface-1)] px-6 py-3">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-[var(--series-1)] text-sm font-bold text-white">
              IP
            </div>
            <span className="text-sm font-semibold">Customer Portal</span>
          </div>
          <nav className="flex gap-1">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                    isActive ? "bg-[var(--series-1)]/10 text-[var(--series-1)]" : "text-[var(--text-secondary)] hover:bg-black/5"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm text-[var(--text-secondary)]">{user?.first_name} {user?.last_name}</span>
          <button onClick={logout} className="text-sm font-medium text-[var(--series-1)] hover:underline">
            Sign out
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-5xl p-8">
        <Outlet />
      </main>
    </div>
  );
}
