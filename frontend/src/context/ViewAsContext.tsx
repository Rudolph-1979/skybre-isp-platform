import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { useAuth } from "./AuthContext";

// "View customer portal" for staff: renders the real customer portal
// against a chosen customer, so support can see exactly what the customer
// sees.
//
// NOTE ON WHY THIS IS NOT IMPERSONATION. No customer token is minted and
// the staff member keeps their own identity and their own JWT. All this
// holds is *which customer's id the portal pages should ask about* -- and
// the portal endpoints (/customers, /invoices, /payments, /services,
// /tickets) already accept `?customer=<id>` and already let staff read any
// customer. So this grants a staff member no access they didn't already
// have through the admin UI; it only renders the same data in the
// customer's layout.
//
// That has three consequences worth keeping:
//   * nothing to audit beyond what already exists, because no action is
//     ever taken as the customer;
//   * no repudiation risk -- a ticket can't appear that the customer
//     didn't create;
//   * a customer tampering with the stored value gains nothing, because
//     the backend scopes every one of those endpoints to their own
//     customer_profile regardless of what they ask for.
//
// Writes are hidden while viewing, since submitting anything here would
// record the staff member as the author on the customer's ticket.

const STORAGE_KEY = "skybre_view_as_customer";

export type ViewAsTarget = { id: number; name: string };

type ViewAsValue = {
  target: ViewAsTarget | null;
  startViewing: (target: ViewAsTarget) => void;
  stopViewing: () => void;
  /** The customer id portal pages should load: the view-as target for
   *  staff, otherwise the signed-in customer's own id. */
  effectiveCustomerId: number | undefined;
};

const ViewAsContext = createContext<ViewAsValue>({
  target: null,
  startViewing: () => {},
  stopViewing: () => {},
  effectiveCustomerId: undefined,
});

function readStored(): ViewAsTarget | null {
  try {
    // sessionStorage, not localStorage: viewing as a customer should not
    // outlive the tab. Coming back tomorrow to a portal that silently
    // isn't yours is how mistakes happen.
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return typeof parsed?.id === "number" ? { id: parsed.id, name: String(parsed.name ?? "") } : null;
  } catch {
    return null;
  }
}

export function ViewAsProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [target, setTarget] = useState<ViewAsTarget | null>(readStored);

  const isStaff = !!user && user.role !== "customer";

  const startViewing = useCallback((next: ViewAsTarget) => {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    setTarget(next);
  }, []);

  const stopViewing = useCallback(() => {
    sessionStorage.removeItem(STORAGE_KEY);
    setTarget(null);
  }, []);

  const value = useMemo<ViewAsValue>(() => {
    // Ignore any stored target for a customer account. It would achieve
    // nothing (the backend scopes their data to themselves), but the portal
    // shouldn't render a "viewing as" banner to a customer.
    const effectiveTarget = isStaff ? target : null;
    return {
      target: effectiveTarget,
      startViewing,
      stopViewing,
      effectiveCustomerId: effectiveTarget?.id ?? user?.customer_id,
    };
  }, [isStaff, target, startViewing, stopViewing, user?.customer_id]);

  return <ViewAsContext.Provider value={value}>{children}</ViewAsContext.Provider>;
}

export function useViewAs() {
  return useContext(ViewAsContext);
}
