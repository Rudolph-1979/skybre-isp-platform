import type { Partner, Role } from "../types";

// In User.allowed_partners, 0 stands for "direct customers (no partner)"
// (customers.scoping.DIRECT on the backend). Partner ids start at 1.
export const DIRECT_PARTNER = 0;

// Rudolph, 5 Oct 2026: a restricted user sees only the partners ticked for
// them, and direct customers only when "Direct" is ticked. Unrestricted
// (empty list) and Administrators see everything.
export function canSeeDirect(user: { role: Role; allowed_partners: number[] } | null | undefined): boolean {
  if (!user || user.role === "admin" || user.allowed_partners.length === 0) return true;
  return user.allowed_partners.includes(DIRECT_PARTNER);
}

export function partnerAccessSummary(
  entry: { role: Role; allowed_partners: number[] },
  partners: Partner[],
): string {
  if (entry.role === "admin") return "Sees all partners (Admin)";
  if (entry.allowed_partners.length === 0) return "Sees all partners";
  const names = partners.filter((p) => entry.allowed_partners.includes(p.id)).map((p) => p.name);
  if (entry.allowed_partners.includes(DIRECT_PARTNER)) names.push("Direct");
  return names.join(", ") || `${entry.allowed_partners.length} partner(s)`;
}

// The "Restrict to selected partners" block, shared by Configs -> Users
// (Edit user) and Configs -> Permissions so the two can't drift apart.
export function PartnerAccessFields({
  partners,
  role,
  restricted,
  onRestrictedChange,
  checked,
  onToggle,
}: {
  partners: Partner[];
  role: Role;
  restricted: boolean;
  onRestrictedChange: (value: boolean) => void;
  checked: Set<number>;
  onToggle: (id: number) => void;
}) {
  const options = [{ id: DIRECT_PARTNER, name: "Direct customers (no partner)" }, ...partners];
  return (
    <div className="mb-3">
      <p className="mb-1 text-sm font-medium text-[var(--text-secondary)]">Partner access</p>
      {role === "admin" ? (
        <p className="text-xs text-[var(--text-muted)]">
          Administrators always see every partner. Change the role to restrict this user.
        </p>
      ) : (
        <>
          <label className="mb-2 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={restricted} onChange={(e) => onRestrictedChange(e.target.checked)} />
            <span>Restrict to selected partners only</span>
          </label>
          <div className={`grid grid-cols-1 gap-2 sm:grid-cols-2 ${restricted ? "" : "opacity-40"}`}>
            {options.map((p) => (
              <label key={p.id} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  disabled={!restricted}
                  checked={checked.has(p.id)}
                  onChange={() => onToggle(p.id)}
                />
                <span>{p.name}</span>
              </label>
            ))}
          </div>
          {restricted ? (
            checked.size === 0 ? (
              <p className="mt-2 text-xs text-[var(--status-warning,#b45309)]">
                Nothing ticked — this has the same effect as "Sees all partners" until at least one is checked.
              </p>
            ) : (
              <p className="mt-2 text-xs text-[var(--text-muted)]">
                They see only what is ticked: customers, invoices, payments, tickets, jobs and reports.
                {!checked.has(DIRECT_PARTNER) && " Direct customers are hidden from them."}
              </p>
            )
          ) : (
            <p className="mt-2 text-xs text-[var(--text-muted)]">Sees every partner and direct customers.</p>
          )}
        </>
      )}
    </div>
  );
}
