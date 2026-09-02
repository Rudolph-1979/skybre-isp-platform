import { FormField, inputClass } from "./Modal";
import type { Device, ConnectionRule } from "../types";

// Shared by the "New service"/"Edit service" forms (Services page and a
// customer's own detail page) -- lets staff assign a service to a router
// and, optionally, override its tariff's plan speed with one of that
// router's Connection Rules. This is what the live-API blocking/shaper
// features (network.router_sync) actually key off: a service with no
// device assigned here is invisible to all of them.

export type ServiceShapingValues = {
  device: string;
  connection_rule: string;
  // Where the client physically connects -- see the Access block below.
  // Nothing is enforced against these; they document the path.
  access_device: string;
  access_detail: string;
  // Fair-use overrides. Blank = whatever the plan says.
  fup_threshold_gb: string;
  fup_speed_pct: string;
  fup_exempt: boolean;
};

export const emptyServiceShapingValues: ServiceShapingValues = {
  device: "", connection_rule: "", access_device: "", access_detail: "",
  fup_threshold_gb: "", fup_speed_pct: "", fup_exempt: false,
};

export function serviceShapingPayload(values: ServiceShapingValues): Record<string, unknown> {
  return {
    device: values.device ? Number(values.device) : null,
    connection_rule: values.connection_rule ? Number(values.connection_rule) : null,
    access_device: values.access_device ? Number(values.access_device) : null,
    access_detail: values.access_detail,
    // "" means "use the plan's", which is null on the wire. Number("")
    // is 0, and 0 is a REAL threshold here meaning shape immediately --
    // so the empty check has to come first.
    fup_threshold_gb: values.fup_threshold_gb === "" ? null : Number(values.fup_threshold_gb),
    fup_speed_pct: values.fup_speed_pct === "" ? null : Number(values.fup_speed_pct),
    fup_exempt: values.fup_exempt,
  };
}

// Access-side hardware first. Putting routers at the top of a list meant
// for picking a sector is how the wrong box gets chosen.
const ACCESS_TYPE_ORDER = ["access_point", "olt", "switch", "onu", "router", "server"];

const DEVICE_TYPE_LABEL: Record<string, string> = {
  access_point: "AP",
  olt: "OLT",
  switch: "Switch",
  onu: "ONU/CPE",
  router: "Router",
  server: "Server",
};

/** Grouped by site, because "Tower 3" is how somebody describes where a
 *  client points, and a flat list of forty radio names is not. */
function accessDeviceGroups(devices: Device[]) {
  const bySite = new Map<string, Device[]>();
  for (const d of devices) {
    const key = d.site_name || "No site set";
    if (!bySite.has(key)) bySite.set(key, []);
    bySite.get(key)!.push(d);
  }
  return [...bySite.entries()]
    .sort(([a], [b]) => (a === "No site set" ? 1 : b === "No site set" ? -1 : a.localeCompare(b)))
    .map(([site, list]) => ({
      site,
      devices: [...list].sort((a, b) => {
        const rank =
          ACCESS_TYPE_ORDER.indexOf(a.device_type) - ACCESS_TYPE_ORDER.indexOf(b.device_type);
        return rank !== 0 ? rank : a.name.localeCompare(b.name);
      }),
    }));
}

export function ServiceShapingFields({
  values,
  onChange,
  devices,
  connectionRules,
}: {
  values: ServiceShapingValues;
  onChange: (patch: Partial<ServiceShapingValues>) => void;
  devices: Device[];
  connectionRules: ConnectionRule[];
}) {
  const rulesForDevice = values.device ? connectionRules.filter((r) => String(r.device) === values.device) : [];

  return (
    <div className="mt-4 border-t border-[var(--border-hairline)] pt-4">
      <p className="mb-2 text-sm font-semibold text-[var(--text-primary)]">Router & shaping</p>
      <p className="mb-3 text-xs text-[var(--text-muted)]">
        Which router this service is on. Needed for that router's blocking-on-suspend, shaper, and live bandwidth
        features (Networking page) to apply to this specific customer.
      </p>
      <FormField label="Router">
        <select
          className={inputClass}
          value={values.device}
          onChange={(e) => onChange({ device: e.target.value, connection_rule: "" })}
        >
          <option value="">No router assigned</option>
          {devices.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name} ({d.ip_address})
            </option>
          ))}
        </select>
      </FormField>
      {values.device && (
        <FormField label="Speed override (optional)">
          <select
            className={inputClass}
            value={values.connection_rule}
            onChange={(e) => onChange({ connection_rule: e.target.value })}
          >
            <option value="">Use the tariff's plan speed</option>
            {rulesForDevice.map((r) => (
              <option key={r.id} value={r.id}>
                {r.title} ({r.speed_down_kbps}/{r.speed_up_kbps} kbps)
              </option>
            ))}
          </select>
        </FormField>
      )}

      {/* A separate block from the router above, because they answer
          different questions and conflating them is the mistake this is
          here to prevent. The router is where the line TERMINATES and
          where every RADIUS/shaper/blocking action is pushed. The access
          device is where the client physically CONNECTS -- which on a
          wireless network is a sector radio on a tower, not the core
          router every customer terminates on. */}
      <div className="mt-5 border-t border-[var(--border-hairline)] pt-4">
        <p className="mb-2 text-sm font-semibold text-[var(--text-primary)]">Access — where the client connects</p>
        <p className="mb-3 text-xs text-[var(--text-muted)]">
          The AP, sector, OLT or switch this client actually connects to. Nothing is enforced
          against it — it's here so that when this device goes down, the Networking page can tell
          you who to phone.
        </p>
        <FormField label="Access device">
          <select
            className={inputClass}
            value={values.access_device}
            onChange={(e) => onChange({ access_device: e.target.value })}
          >
            <option value="">Not recorded</option>
            {accessDeviceGroups(devices).map((group) => (
              <optgroup key={group.site} label={group.site}>
                {group.devices.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} — {DEVICE_TYPE_LABEL[d.device_type] ?? d.device_type}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </FormField>
        <FormField
          label="Port / sector / SSID"
          hint='Optional. e.g. "Sector B 120°", "PON 1/3", "ether7".'
        >
          <input
            className={inputClass}
            value={values.access_detail}
            maxLength={120}
            onChange={(e) => onChange({ access_detail: e.target.value })}
          />
        </FormField>
      </div>

      {/* Exceptions only. The plan owns the policy; this is here so one
          business line or one staff account can be treated differently
          without inventing a whole new tariff for them. */}
      <div className="mt-5 border-t border-[var(--border-hairline)] pt-4">
        <p className="mb-2 text-sm font-semibold text-[var(--text-primary)]">Fair use — this line only</p>
        <p className="mb-3 text-xs text-[var(--text-muted)]">
          Leave blank to use whatever the plan says. Fill these in only to treat this one
          customer differently.
        </p>
        <label className="mb-3 flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={values.fup_exempt}
            onChange={(e) => onChange({ fup_exempt: e.target.checked })}
          />
          <span>
            <span className="font-medium text-[var(--text-secondary)]">Never shape this line</span>
            <span className="mt-0.5 block text-xs text-[var(--text-muted)]">
              For business lines and staff. Overrides everything below.
            </span>
          </span>
        </label>
        {!values.fup_exempt && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <FormField label="Threshold (GB / month)" hint="Blank = use the plan's.">
              <input
                type="number"
                min={0}
                className={inputClass}
                placeholder="Plan default"
                value={values.fup_threshold_gb}
                onChange={(e) => onChange({ fup_threshold_gb: e.target.value })}
              />
            </FormField>
            <FormField label="Shaped speed (% of plan)" hint="Blank = use the plan's.">
              <input
                type="number"
                min={1}
                max={100}
                className={inputClass}
                placeholder="Plan default"
                value={values.fup_speed_pct}
                onChange={(e) => onChange({ fup_speed_pct: e.target.value })}
              />
            </FormField>
          </div>
        )}
      </div>
    </div>
  );
}
