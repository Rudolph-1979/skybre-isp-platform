import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { Conecto } from "./conecto/Conecto";
import { useViewAs } from "../context/ViewAsContext";
import { ThemeToggle } from "./ThemeToggle";
import skybreIcon from "../assets/skybre-icon.png";

const NAV = [
  { to: "/portal", label: "Overview", end: true },
  { to: "/portal/invoices", label: "Invoices & Payments" },
  { to: "/portal/tickets", label: "Support Tickets" },
];

export function PortalLayout() {
  const { user, logout } = useAuth();
  const { target: viewingAs, stopViewing } = useViewAs();
  const navigate = useNavigate();
  // The logo + 3 nav links + name + sign-out all fit comfortably on one
  // row on desktop, but not at phone widths -- below sm this collapses
  // into a hamburger dropdown instead of wrapping/overflowing.
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="min-h-screen bg-[var(--page-plane)]">
      {viewingAs && (
        // Unmissable and always on screen. A staff member who forgets which
        // mode they're in is the only real hazard here, so the banner is
        // sticky rather than something that scrolls away.
        <div className="sticky top-0 z-30 flex flex-wrap items-center justify-between gap-2 bg-[#8a5a00] px-4 py-2 text-sm text-white sm:px-6">
          <span>
            Viewing the portal as <strong>{viewingAs.name}</strong> — read only. Nothing you do here is
            recorded as the customer.
          </span>
          <button
            type="button"
            className="rounded bg-white/20 px-2 py-1 text-xs font-medium hover:bg-white/30"
            onClick={() => {
              stopViewing();
              navigate(`/admin/customers/${viewingAs.id}`);
            }}
          >
            Exit customer view
          </button>
        </div>
      )}
      <header className="border-b border-[var(--border-hairline)] bg-[var(--surface-1)] px-4 py-3 sm:px-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <img src={skybreIcon} alt="Skybre" className="h-8 w-8 object-contain" />
            <span className="text-sm font-semibold text-[var(--text-primary)]">Skybre Customer Portal</span>
          </div>

          <nav className="hidden items-center gap-1 sm:flex">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                    isActive ? "bg-[var(--series-1)]/10 text-[var(--series-1)]" : "text-[var(--text-secondary)] hover:bg-[var(--tint-hover)]"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="hidden items-center gap-3 sm:flex">
            <ThemeToggle />
            <span className="text-sm text-[var(--text-secondary)]">
              {viewingAs ? viewingAs.name : `${user?.first_name ?? ""} ${user?.last_name ?? ""}`}
            </span>
            {/* Signing out here would end the STAFF member's session, which
                is not what "leave the customer's portal" should mean. */}
            {!viewingAs && (
              <button onClick={logout} className="text-sm font-medium text-[var(--series-1)] hover:underline">
                Sign out
              </button>
            )}
          </div>

          <button
            type="button"
            aria-label={menuOpen ? "Close menu" : "Open menu"}
            onClick={() => setMenuOpen((o) => !o)}
            className="rounded-md p-2 text-xl leading-none text-[var(--text-secondary)] hover:bg-[var(--tint-hover)] sm:hidden"
          >
            {menuOpen ? "✕" : "☰"}
          </button>
        </div>

        {menuOpen && (
          <div className="mt-3 space-y-2 border-t border-[var(--border-hairline)] pt-3 sm:hidden">
            <nav className="space-y-0.5">
              {NAV.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  onClick={() => setMenuOpen(false)}
                  className={({ isActive }) =>
                    `block rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                      isActive ? "bg-[var(--series-1)]/10 text-[var(--series-1)]" : "text-[var(--text-secondary)] hover:bg-[var(--tint-hover)]"
                    }`
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </nav>
            <div className="flex items-center justify-between border-t border-[var(--border-hairline)] px-3 pt-3">
              <div>
                <p className="text-sm text-[var(--text-secondary)]">{user?.first_name} {user?.last_name}</p>
                <button onClick={logout} className="text-sm font-medium text-[var(--series-1)] hover:underline">
                  Sign out
                </button>
              </div>
              <ThemeToggle />
            </div>
          </div>
        )}
      </header>
      <main className="mx-auto max-w-5xl p-4 sm:p-8">
        <Outlet />
      </main>
      {/* Customer-facing assistant. Separate message set from the staff one —
          a customer must never see internal figures or internal wording. */}
      <Conecto audience="customer" />
    </div>
  );
}
