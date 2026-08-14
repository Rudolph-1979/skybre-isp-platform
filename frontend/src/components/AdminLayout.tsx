import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { ThemeToggle } from "./ThemeToggle";
import skybreIcon from "../assets/skybre-icon.png";

const NAV = [
  { to: "/admin", label: "Dashboard", end: true },
  { to: "/admin/scheduling", label: "Scheduling" },
  { to: "/admin/customers", label: "Customers" },
  { to: "/admin/services", label: "Services" },
  { to: "/admin/tariffs", label: "Tariffs" },
  { to: "/admin/invoices", label: "Invoices" },
  { to: "/admin/payments", label: "Payments" },
  { to: "/admin/inventory", label: "Stock / Inventory" },
  { to: "/admin/devices", label: "Network Devices" },
  { to: "/admin/ip-pools", label: "IP Address Pools" },
  { to: "/admin/tickets", label: "Support Tickets" },
];

const EMAIL_NAV = [
  { to: "/admin/bulk-email", label: "Bulk Email" },
  { to: "/admin/email-templates", label: "Email Templates" },
];

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  `block rounded-md px-3 py-2 text-sm font-medium transition-colors ${
    isActive
      ? "bg-[var(--series-1)]/10 text-[var(--series-1)]"
      : "text-[var(--text-secondary)] hover:bg-[var(--tint-hover)]"
  }`;

export function AdminLayout() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const [emailOpen, setEmailOpen] = useState(() => EMAIL_NAV.some((item) => location.pathname.startsWith(item.to)));

  return (
    <div className="flex min-h-screen bg-[var(--page-plane)]">
      <aside className="flex w-60 flex-col border-r border-[var(--border-hairline)] bg-[var(--surface-1)]">
        <div className="flex items-center justify-between px-5 py-5">
          <div className="flex items-center gap-2">
            <img src={skybreIcon} alt="Skybre" className="h-8 w-8 object-contain" />
            <span className="text-sm font-semibold text-[var(--text-primary)]">Skybre</span>
          </div>
          <ThemeToggle />
        </div>
        <nav className="flex-1 space-y-0.5 px-3">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={navLinkClass}>
              {item.label}
            </NavLink>
          ))}

          <button
            type="button"
            onClick={() => setEmailOpen((o) => !o)}
            className="flex w-full items-center justify-between rounded-md px-3 py-2 text-sm font-medium text-[var(--text-secondary)] transition-colors hover:bg-[var(--tint-hover)]"
          >
            Email
            <span className={`text-[10px] transition-transform ${emailOpen ? "rotate-90" : ""}`}>▶</span>
          </button>
          {emailOpen && (
            <div className="ml-3 space-y-0.5 border-l border-[var(--border-hairline)] pl-3">
              {EMAIL_NAV.map((item) => (
                <NavLink key={item.to} to={item.to} className={navLinkClass}>
                  {item.label}
                </NavLink>
              ))}
            </div>
          )}
        </nav>
        <div className="border-t border-[var(--border-hairline)] p-4">
          <p className="text-sm font-medium text-[var(--text-primary)]">{user?.first_name} {user?.last_name}</p>
          <p className="text-xs capitalize text-[var(--text-muted)]">{user?.role}</p>
          <NavLink
            to="/admin/account"
            className="mt-2 block text-xs font-medium text-[var(--series-1)] hover:underline"
          >
            Account settings
          </NavLink>
          <button
            onClick={logout}
            className="mt-1 text-xs font-medium text-[var(--series-1)] hover:underline"
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
