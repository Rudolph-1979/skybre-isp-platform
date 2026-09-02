import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { Conecto } from "./conecto/Conecto";
import { ThemeToggle } from "./ThemeToggle";
import { canAccessSection } from "../utils/permissions";
import type { Section, User } from "../types";
import skybreIcon from "../assets/skybre-icon.png";

type NavItem = {
  to: string;
  label: string;
  end?: boolean;
  section?: Section;
  /** Nested entries, shown indented under this one. One level only. */
  children?: NavItem[];
};

// `section` is omitted for Dashboard -- it's the landing page and always
// visible to every staff member, regardless of allowed_sections.
//
// Note that a child keeps its OWN section gate; it does not inherit the
// parent's. Support Tickets sits under Customers visually but is still
// gated on `tickets`, so somebody with Tickets access and no Customers
// access keeps it (see buildNav below for how that case renders).
const NAV: NavItem[] = [
  { to: "/admin", label: "Dashboard", end: true },
  { to: "/admin/scheduling", label: "Scheduling", section: "scheduling" },
  // Above Customers, because that is the order the lifecycle runs in: an
  // enquiry becomes a customer, not the other way round.
  { to: "/admin/leads", label: "Leads", section: "sales" },
  {
    to: "/admin/customers",
    label: "Customers",
    section: "customers",
    children: [
      { to: "/admin/usage-report", label: "Usage report", section: "customers" },
      { to: "/admin/offline-customers", label: "Recently offline", section: "customers" },
      { to: "/admin/tickets", label: "Support Tickets", section: "tickets" },
    ],
  },
  { to: "/admin/services", label: "Services", section: "services" },
  { to: "/admin/finance", label: "Finance", section: "finance" },
  { to: "/admin/accountant", label: "Accountant", section: "accountant" },
  { to: "/admin/inventory", label: "Stock / Inventory", section: "inventory" },
  { to: "/admin/networking", label: "Networking", section: "networking" },
  { to: "/admin/vehicles", label: "Vehicles", section: "vehicles" },
  { to: "/admin/bulk-email", label: "Email", section: "bulk_email" },
  { to: "/admin/configs", label: "Configs", section: "configs" },
];

function permitted(user: User | null | undefined, item: NavItem): boolean {
  return !item.section || canAccessSection(user, item.section);
}

/**
 * Flattens NAV into what this particular user should see.
 *
 * The interesting case is a parent the user can't reach that has children
 * they can. Rather than hide the children with the parent (losing access)
 * or show a "Customers" heading to somebody with no customer access
 * (misleading), the permitted children are promoted to top level.
 */
function buildNav(user: User | null | undefined, items: NavItem[]): NavItem[] {
  const out: NavItem[] = [];
  for (const item of items) {
    const children = (item.children ?? []).filter((child) => permitted(user, child));
    if (permitted(user, item)) {
      out.push(children.length ? { ...item, children } : { ...item, children: undefined });
    } else {
      out.push(...children.map((child) => ({ ...child })));
    }
  }
  return out;
}

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  `block rounded-md px-3 py-2 text-sm font-medium transition-colors ${
    isActive
      ? "bg-[var(--series-1)]/10 text-[var(--series-1)]"
      : "text-[var(--text-secondary)] hover:bg-[var(--tint-hover)]"
  }`;

const childLinkClass = ({ isActive }: { isActive: boolean }) =>
  `block rounded-md py-1.5 pl-3 pr-3 text-sm transition-colors ${
    isActive
      ? "bg-[var(--series-1)]/10 font-medium text-[var(--series-1)]"
      : "text-[var(--text-secondary)] hover:bg-[var(--tint-hover)]"
  }`;

function matchesPath(pathname: string, to: string): boolean {
  return pathname === to || pathname.startsWith(`${to}/`);
}

function NavGroup({
  item,
  onNavigate,
}: {
  item: NavItem;
  onNavigate: () => void;
}) {
  const { pathname } = useLocation();
  const children = item.children ?? [];
  const childActive = children.some((child) => matchesPath(pathname, child.to));
  // Open by default whenever we're somewhere inside the group; the chevron
  // records an explicit override that wins from then on.
  const [override, setOverride] = useState<boolean | null>(null);
  const open = override ?? (childActive || matchesPath(pathname, item.to));

  return (
    <div>
      {/* The chevron sits ON the link row rather than beside it, so the
          active pill is the same full width as every other nav item. */}
      <div className="relative">
        <NavLink
          to={item.to}
          end={item.end}
          className={({ isActive }) => `${navLinkClass({ isActive })} pr-9`}
          onClick={() => {
            setOverride(true);
            onNavigate();
          }}
        >
          {item.label}
        </NavLink>
        <button
          type="button"
          onClick={() => setOverride(!open)}
          aria-expanded={open}
          aria-label={open ? `Collapse ${item.label}` : `Expand ${item.label}`}
          className="absolute right-1 top-1/2 -translate-y-1/2 rounded-md px-1.5 py-1 text-[var(--text-muted)] transition-colors hover:bg-[var(--tint-hover)]"
        >
          <span
            aria-hidden="true"
            className={`block text-[10px] leading-none transition-transform duration-150 ${
              open ? "rotate-90" : ""
            }`}
          >
            ▶
          </span>
        </button>
      </div>
      {open && (
        <div className="mt-0.5 space-y-0.5 border-l border-[var(--border-hairline)] pl-2 ml-4">
          {children.map((child) => (
            <NavLink
              key={child.to}
              to={child.to}
              end={child.end}
              className={childLinkClass}
              onClick={onNavigate}
            >
              {child.label}
            </NavLink>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * The sidebar link list on its own. Exported (and taking `user` as a prop
 * rather than reading the auth context) so it can be rendered in isolation
 * -- both layouts below use it, and it can be previewed without standing up
 * the whole authenticated app.
 */
export function SidebarNav({
  user,
  onNavigate,
}: {
  user: User | null | undefined;
  onNavigate: () => void;
}) {
  const visibleNav = buildNav(user, NAV);
  return (
    <>
      {visibleNav.map((item) =>
        item.children?.length ? (
          <NavGroup key={item.to} item={item} onNavigate={onNavigate} />
        ) : (
          <NavLink key={item.to} to={item.to} end={item.end} className={navLinkClass} onClick={onNavigate}>
            {item.label}
          </NavLink>
        ),
      )}
    </>
  );
}

export function AdminLayout() {
  const { user, logout } = useAuth();
  // Below the md breakpoint the sidebar becomes a slide-over drawer,
  // toggled from the mobile top bar, instead of the always-visible fixed
  // column it is on desktop.
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  function closeMobileNav() {
    setMobileNavOpen(false);
  }

  return (
    <div className="flex min-h-screen flex-col bg-[var(--page-plane)] md:flex-row">
      {/* Mobile-only top bar with the hamburger toggle -- hidden on md+
          where the sidebar is always visible instead. */}
      <div className="flex items-center justify-between border-b border-[var(--border-hairline)] bg-[var(--surface-1)] px-4 py-3 md:hidden">
        <div className="flex items-center gap-2">
          <img src={skybreIcon} alt="Skybre" className="h-7 w-7 object-contain" />
          <span className="text-sm font-semibold text-[var(--text-primary)]">Skybre</span>
        </div>
        <div className="flex items-center gap-1">
          <ThemeToggle />
          <button
            type="button"
            aria-label="Open menu"
            onClick={() => setMobileNavOpen(true)}
            className="rounded-md p-2 text-xl leading-none text-[var(--text-secondary)] hover:bg-[var(--tint-hover)]"
          >
            ☰
          </button>
        </div>
      </div>

      {/* Backdrop -- only rendered (and only needed) while the mobile
          drawer is open; tapping it closes the drawer, same as clicking
          outside a modal. */}
      {mobileNavOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/40 md:hidden"
          onClick={closeMobileNav}
          aria-hidden="true"
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-72 flex-col border-r border-[var(--border-hairline)] bg-[var(--surface-1)] transition-transform duration-200 ease-in-out md:static md:z-auto md:w-60 md:translate-x-0 ${
          mobileNavOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between px-5 py-5">
          <div className="flex items-center gap-2">
            <img src={skybreIcon} alt="Skybre" className="h-8 w-8 object-contain" />
            <span className="text-sm font-semibold text-[var(--text-primary)]">Skybre</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="hidden md:block">
              <ThemeToggle />
            </div>
            <button
              type="button"
              aria-label="Close menu"
              onClick={closeMobileNav}
              className="rounded-md p-1 text-lg leading-none text-[var(--text-secondary)] hover:bg-[var(--tint-hover)] md:hidden"
            >
              ✕
            </button>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-3">
          <SidebarNav user={user} onNavigate={closeMobileNav} />
        </nav>
        <div className="border-t border-[var(--border-hairline)] p-4">
          <p className="text-sm font-medium text-[var(--text-primary)]">{user?.first_name} {user?.last_name}</p>
          <p className="text-xs capitalize text-[var(--text-muted)]">{user?.role}</p>
          <NavLink
            to="/admin/account"
            className="mt-2 block text-xs font-medium text-[var(--series-1)] hover:underline"
            onClick={closeMobileNav}
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
      <main className="flex-1 overflow-y-auto p-4 md:p-8">
        <Outlet />
      </main>
      {/* Staff-facing assistant. Fixed-position, so it sits outside the
          scrolling main area rather than inside it. */}
      <Conecto audience="staff" />
    </div>
  );
}
