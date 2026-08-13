import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

const NAV = [
  { to: "/admin", label: "Dashboard", end: true },
  { to: "/admin/customers", label: "Customers" },
  { to: "/admin/services", label: "Services" },
  { to: "/admin/tariffs", label: "Tariffs" },
  { to: "/admin/invoices", label: "Invoices" },
  { to: "/admin/payments", label: "Payments" },
  { to: "/admin/devices", label: "Network Devices" },
  { to: "/admin/ip-pools", label: "IP Address Pools" },
  { to: "/admin/tickets", label: "Support Tickets" },
];

export function AdminLayout() {
  const { user, logout } = useAuth();

  return (
    <div className="flex min-h-screen bg-[var(--page-plane)]">
      <aside className="flex w-60 flex-col border-r border-[var(--border-hairline)] bg-[var(--surface-1)]">
        <div className="flex items-center gap-2 px-5 py-5">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-[var(--series-1)] text-sm font-bold text-white">
            IP
          </div>
          <span className="text-sm font-semibold text-[var(--text-primary)]">ISP Platform</span>
        </div>
        <nav className="flex-1 space-y-0.5 px-3">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `block rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-[var(--series-1)]/10 text-[var(--series-1)]"
                    : "text-[var(--text-secondary)] hover:bg-black/5"
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-[var(--border-hairline)] p-4">
          <p className="text-sm font-medium text-[var(--text-primary)]">{user?.first_name} {user?.last_name}</p>
          <p className="text-xs capitalize text-[var(--text-muted)]">{user?.role}</p>
          <button
            onClick={logout}
            className="mt-2 text-xs font-medium text-[var(--series-1)] hover:underline"
          >
            Sign out
          </button>
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto p-8">
        <Outlet />
      </main>
    </div>
  );
}
