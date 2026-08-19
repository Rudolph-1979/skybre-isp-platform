import { FormField, inputClass } from "./Modal";
import type { IPPool, IPAddress, ServiceConnectionType, IPAssignmentMode } from "../types";

// Shared by the "New service" and "Edit service" forms (on both the
// standalone Services page and a customer's own detail page) so the
// RADIUS/PPPoE-IP-assignment fields -- and the payload built from them --
// stay in exactly one place instead of drifting across four copies.

export type ServiceConnectionValues = {
  radius_username: string;
  radius_password: string;
  radius_connection_type: ServiceConnectionType;
  ip_assignment_mode: IPAssignmentMode;
  static_ip: string;
  ip_pool: string;
  // Only sent for ip_assignment_mode="pool" -- a specific network.IPAddress
  // id the staff member picked. Left blank to keep whatever's currently
  // assigned untouched (edit) or to require a pick before saving (create,
  // enforced by the backend's serializer validation).
  ip_address: string;
};

export const emptyServiceConnectionValues: ServiceConnectionValues = {
  radius_username: "", radius_password: "",
  radius_connection_type: "ovpn", ip_assignment_mode: "auto", static_ip: "", ip_pool: "", ip_address: "",
};

/** Builds the subset of the Service PATCH/POST payload these fields are
 * responsible for. Callers merge this with their own customer/tariff/status
 * fields. */
export function serviceConnectionPayload(values: ServiceConnectionValues): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    radius_username: values.radius_username || null,
    // Blank means "leave the existing secret as-is" on edit (the backend
    // ignores an empty radius_password on update rather than clearing it) --
    // on create, blank just means "no RADIUS login yet".
    radius_password: values.radius_password,
    radius_connection_type: values.radius_connection_type,
  };
  if (values.radius_connection_type === "pppoe") {
    payload.ip_assignment_mode = values.ip_assignment_mode;
    if (values.ip_assignment_mode === "manual") {
      payload.static_ip = values.static_ip || null;
    } else {
      payload.ip_pool = values.ip_pool ? Number(values.ip_pool) : null;
      if (values.ip_assignment_mode === "pool" && values.ip_address) {
        payload.ip_address = Number(values.ip_address);
      }
    }
  }
  return payload;
}

export function ServiceConnectionFields({
  values,
  onChange,
  customerPools,
  poolAddresses,
  passwordIsSet,
  currentServiceId,
  currentAssignedIp,
}: {
  values: ServiceConnectionValues;
  onChange: (patch: Partial<ServiceConnectionValues>) => void;
  customerPools: IPPool[];
  poolAddresses: IPAddress[];
  // Only meaningful when editing an existing service.
  passwordIsSet?: boolean;
  currentServiceId?: number;
  currentAssignedIp?: string | null;
}) {
  return (
    <>
      <div className="mt-4 border-t border-[var(--border-hairline)] pt-4">
        <p className="mb-2 text-sm font-semibold text-[var(--text-primary)]">RADIUS login</p>
        <p className="mb-3 text-xs text-[var(--text-muted)]">
          Credentials the customer's router authenticates with. On successful login the tariff's speed is applied
          automatically. Leave the username blank to disable RADIUS login for this service.
        </p>
        <FormField label="Connection type">
          <select
            className={inputClass}
            value={values.radius_connection_type}
            onChange={(e) => onChange({ radius_connection_type: e.target.value as ServiceConnectionType })}
          >
            <option value="ovpn">OVPN (Teraco Mikrotik)</option>
            <option value="pppoe">PPPoE</option>
          </select>
        </FormField>
        <FormField label="RADIUS username">
          <input
            className={inputClass}
            value={values.radius_username}
            onChange={(e) => onChange({ radius_username: e.target.value })}
            placeholder="e.g. customer-jhb-014"
            // A plain text field right next to a password field looks like
            // a login form to browsers, which then offer to save/autofill
            // it -- and can bleed a saved value into unrelated fields
            // elsewhere on the site. This is a customer's router
            // credential, not anyone's own login -- nothing to save.
            autoComplete="off"
            name="service-radius-username"
          />
        </FormField>
        <FormField label={`RADIUS password${passwordIsSet ? " (set — leave blank to keep)" : ""}`}>
          <input
            type="password"
            className={inputClass}
            value={values.radius_password}
            onChange={(e) => onChange({ radius_password: e.target.value })}
            placeholder={passwordIsSet ? "••••••••" : "Set a password"}
            // "new-password" (not "off", which Chrome ignores on password
            // fields specifically) is what actually stops Chrome from
            // offering to save this or suggesting a previously-saved one.
            autoComplete="new-password"
            name="service-radius-password"
          />
        </FormField>
      </div>

      {values.radius_connection_type === "pppoe" && (
        <div className="mt-4 border-t border-[var(--border-hairline)] pt-4">
          <p className="mb-2 text-sm font-semibold text-[var(--text-primary)]">PPPoE IP assignment</p>
          <p className="mb-3 text-xs text-[var(--text-muted)]">
            How this customer's public IP is handed out when they connect.
            {currentAssignedIp && (
              <> Currently handing out <strong>{currentAssignedIp}</strong>.</>
            )}
          </p>
          <FormField label="Mode">
            <select
              className={inputClass}
              value={values.ip_assignment_mode}
              onChange={(e) => onChange({ ip_assignment_mode: e.target.value as IPAssignmentMode, ip_address: "" })}
            >
              <option value="manual">Manual — type a static public IP</option>
              <option value="pool">Select from Customer IP Pool</option>
              <option value="auto">Automatically assign from Customer IP Pool</option>
            </select>
          </FormField>

          {values.ip_assignment_mode === "manual" && (
            <FormField label="Static public IP">
              <input
                className={inputClass}
                value={values.static_ip}
                onChange={(e) => onChange({ static_ip: e.target.value })}
                placeholder="e.g. 41.0.0.5"
              />
            </FormField>
          )}

          {(values.ip_assignment_mode === "pool" || values.ip_assignment_mode === "auto") && (
            <>
              <FormField label="Customer IP Pool">
                <select
                  className={inputClass}
                  value={values.ip_pool}
                  onChange={(e) => onChange({ ip_pool: e.target.value, ip_address: "" })}
                >
                  <option value="">Select pool…</option>
                  {customerPools.map((p) => (
                    <option key={p.id} value={p.id}>{p.name} ({p.network_cidr}) — {p.free_count} free</option>
                  ))}
                </select>
              </FormField>
              {values.ip_assignment_mode === "pool" && values.ip_pool && (
                <FormField label="Address">
                  <select
                    className={inputClass}
                    value={values.ip_address}
                    onChange={(e) => onChange({ ip_address: e.target.value })}
                  >
                    <option value="">{currentAssignedIp ? "Keep current address" : "Select an address…"}</option>
                    {poolAddresses
                      .filter((a) => a.status === "free" || a.assigned_service === currentServiceId)
                      .map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.address}{a.assigned_service === currentServiceId ? " (current)" : ""}
                        </option>
                      ))}
                  </select>
                </FormField>
              )}
              {values.ip_assignment_mode === "auto" && (
                <p className="mb-3 text-xs text-[var(--text-muted)]">
                  The system will pick the next free address in this pool and keep reusing it for this customer on
                  every reconnect.
                </p>
              )}
            </>
          )}
        </div>
      )}
    </>
  );
}
