import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, filterSelectClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { ColumnToggle, type ColumnDef } from "../../components/ColumnToggle";
import { useColumnVisibility } from "../../hooks/useColumnVisibility";
import type {
  Device, IPPool, IPAddress, PoolCategory, RadiusNasClient, RadAcctSession,
  NetworkSite, Partner, OvpnClientConnection, OvpnClientConnectionPingStatus,
} from "../../types";
import { POOL_CATEGORY_LABELS, NETWORK_TYPE_LABELS } from "../../types";

type Tab = "devices" | "sites" | "ip-pools" | "vpn-clients" | "live-sessions";

type NewAction = { label: string; onClick: () => void } | null;

// Reusable "N of M selected" checkbox dropdown for a partner multi-select
// -- same interaction pattern as CustomersPage's PartnerFilterDropdown,
// but generalized here since both Devices (visible_partners) and Sites
// (single partner, handled separately) live on this page.
function PartnerMultiSelect({
  allPartners,
  selected,
  onChange,
}: {
  allPartners: Partner[];
  selected: number[];
  onChange: (ids: number[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  // Empty selection means "visible to all" -- shown/defaulted as if every
  // partner were checked, even though the stored value is really [].
  const effectiveSelected = selected.length === 0 ? allPartners.map((p) => p.id) : selected;

  function toggle(id: number) {
    if (effectiveSelected.includes(id)) {
      // Unchecking one out of "all" produces an explicit list of
      // everyone else, not an empty (still-"all") array.
      onChange(effectiveSelected.filter((v) => v !== id));
    } else {
      const next = [...effectiveSelected, id];
      // Checking back up to everyone collapses to [] -- the canonical
      // "unrestricted" encoding, same as everywhere else in this app.
      onChange(next.length === allPartners.length ? [] : next);
    }
  }

  return (
    <div className="relative" ref={ref}>
      <button type="button" className={inputClass + " text-left"} onClick={() => setOpen((o) => !o)}>
        {effectiveSelected.length} of {allPartners.length} selected
      </button>
      {open && (
        <div className="absolute left-0 z-20 mt-1 w-64 rounded-md border border-[var(--border-hairline)] bg-[var(--surface-1)] p-2 shadow-lg">
          {allPartners.map((p) => (
            <label key={p.id} className="flex items-center gap-2 rounded px-2 py-1.5 text-sm cursor-pointer hover:bg-[var(--tint-hover)]">
              <input type="checkbox" checked={effectiveSelected.includes(p.id)} onChange={() => toggle(p.id)} />
              <span>{p.name}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

export function NetworkingPage() {
  // Seeded from the URL, same reason as Finance -- a dashboard tile should
  // land on the tab it's talking about.
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState<Tab>(() => {
    const wanted = searchParams.get("tab");
    return (["devices", "sites", "ip-pools", "vpn-clients", "live-sessions"] as Tab[]).includes(wanted as Tab)
      ? (wanted as Tab)
      : "devices";
  });
  const [newAction, setNewAction] = useState<NewAction>(null);
  // RADIUS Clients (and the default-FreeRADIUS-server setting that used to
  // be the "OVPN" tab) now live under Configs -> RADIUS. The endpoint still
  // requires the `networking` section, so the tab there is gated on
  // networking access rather than configs access -- moving the screen
  // deliberately did not change who can see it.
  const TABS: { key: Tab; label: string }[] = [
    { key: "devices", label: "Routers" },
    { key: "sites", label: "Sites" },
    { key: "ip-pools", label: "IP Pools" },
    { key: "vpn-clients", label: "OpenVPN" },
    { key: "live-sessions", label: "Live Sessions" },
  ];

  return (
    <div>
      <PageHeader
        title="Networking"
        subtitle="Routers, monitoring, and IPv4/IPv6 address pools."
        actions={
          newAction && (
            <button className={btnPrimary} onClick={newAction.onClick}>
              {newAction.label}
            </button>
          )
        }
      />
      <div className="mb-4 flex gap-1 border-b border-[var(--border-hairline)]">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium ${
              tab === t.key
                ? "border-b-2 border-[var(--series-1)] text-[var(--text-primary)]"
                : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === "devices" && <DevicesTab onRegisterNewAction={setNewAction} />}
      {tab === "sites" && <SitesTab onRegisterNewAction={setNewAction} />}
      {tab === "ip-pools" && <IPPoolsTab onRegisterNewAction={setNewAction} />}
      {tab === "vpn-clients" && <VpnClientsTab onRegisterNewAction={setNewAction} />}
      {tab === "live-sessions" && <LiveSessionsTab onRegisterNewAction={setNewAction} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Devices
// ---------------------------------------------------------------------------

const EMPTY_DEVICE: Partial<Device> = {
  name: "",
  device_type: "router",
  ip_address: "",
  location: "",
  site: null,
  vendor: "",
  model_name: "",
  visible_partners: [],
};

type DeviceFormState = Partial<Device> & { api_password?: string };

const emptyDeviceForm: DeviceFormState = {
  ...EMPTY_DEVICE,
  api_enabled: false,
  api_port: 8728,
  api_username: "",
  api_password: "",
  api_use_ssl: false,
  api_wan_interface: "",
  block_disabled_customers: false,
  enable_shaper: false,
  enable_wireless_access_list: false,
  enable_mpsk: false,
  wireless_interface: "",
};

const DEVICE_COLUMNS: ColumnDef[] = [
  { key: "name", label: "Name" },
  { key: "type", label: "Type" },
  { key: "ip_address", label: "IP Address" },
  { key: "location", label: "Location" },
  { key: "site", label: "Site" },
  { key: "status", label: "Status" },
  { key: "latency", label: "Latency" },
  { key: "bandwidth", label: "Bandwidth (in/out)" },
  { key: "partners", label: "Partners" },
];

function DevicesTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const navigate = useNavigate();
  const [pollingDevice, setPollingDevice] = useState<number | null>(null);
  const [typeFilter, setTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("devices", ["name"]);
  const { items, loading, refetch } = useApiList<Device>(
    `/devices/?page_size=100${typeFilter ? `&device_type=${typeFilter}` : ""}${statusFilter ? `&status=${statusFilter}` : ""}`
  );
  const { items: sites } = useApiList<NetworkSite>("/network-sites/?page_size=200&ordering=title");
  const { items: partners } = useApiList<Partner>("/partners/?page_size=200&ordering=name");
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState<DeviceFormState>(emptyDeviceForm);
  const [saving, setSaving] = useState(false);
  const [editingDevice, setEditingDevice] = useState<Device | null>(null);

  useEffect(() => {
    onRegisterNewAction({ label: "+ New router", onClick: () => openCreate() });
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function openCreate() {
    setEditingDevice(null);
    setForm(emptyDeviceForm);
    setSyncMessage(null);
    setShowModal(true);
  }

  function openEdit(device: Device) {
    setEditingDevice(device);
    setSyncMessage(null);
    setForm({
      name: device.name,
      device_type: device.device_type,
      ip_address: device.ip_address,
      location: device.location,
      site: device.site,
      vendor: device.vendor,
      model_name: device.model_name,
      visible_partners: device.visible_partners,
      api_enabled: device.api_enabled,
      api_port: device.api_port,
      api_username: device.api_username,
      api_password: "",
      api_use_ssl: device.api_use_ssl,
      api_wan_interface: device.api_wan_interface,
      block_disabled_customers: device.block_disabled_customers,
      enable_shaper: device.enable_shaper,
      enable_wireless_access_list: device.enable_wireless_access_list,
      enable_mpsk: device.enable_mpsk,
      wireless_interface: device.wireless_interface,
    });
    setShowModal(true);
  }

  const [syncBusy, setSyncBusy] = useState<string | null>(null);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);

  async function handleSyncBlockingRules(device: Device) {
    setSyncBusy("blocking");
    setSyncMessage(null);
    try {
      const res = await api.post(`/devices/${device.id}/sync-blocking-rules/`);
      setSyncMessage(`Synced — ${res.data.blocked_ip_count} address(es) currently blocked on the router.`);
    } catch (err: any) {
      setSyncMessage(err?.response?.data?.detail || "Couldn't sync blocking rules to this router.");
    } finally {
      setSyncBusy(null);
    }
  }

  async function handleSyncShaperQueues(device: Device) {
    setSyncBusy("shaper");
    setSyncMessage(null);
    try {
      const res = await api.post(`/devices/${device.id}/sync-shaper-queues/`);
      setSyncMessage(`Synced — ${res.data.queue_count} Simple Queue(s) pushed to the router.`);
    } catch (err: any) {
      setSyncMessage(err?.response?.data?.detail || "Couldn't sync shaper queues to this router.");
    } finally {
      setSyncBusy(null);
    }
  }

  async function handleDeleteAllRules(device: Device) {
    if (
      !confirm(
        `Remove everything this platform has pushed to ${device.name} -- blocking address-list/firewall rules, ` +
          "Simple Queues, and any platform-tagged wireless Access List entries? This can't be undone from here."
      )
    )
      return;
    setSyncBusy("delete-all");
    setSyncMessage(null);
    try {
      await api.post(`/devices/${device.id}/delete-all-rules/`);
      setSyncMessage("Cleared all platform-managed config from the router.");
    } catch (err: any) {
      setSyncMessage(err?.response?.data?.detail || "Couldn't clear config from this router.");
    } finally {
      setSyncBusy(null);
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      if (editingDevice) {
        await api.patch(`/devices/${editingDevice.id}/`, form);
      } else {
        await api.post("/devices/", form);
      }
      setShowModal(false);
      setForm(emptyDeviceForm);
      refetch();
    } finally {
      setSaving(false);
    }
  }

  async function pollNow(device: Device) {
    setPollingDevice(device.id);
    try {
      await api.post(`/devices/${device.id}/poll-now/`);
      refetch();
    } catch (err: any) {
      // A router that can't be reached is a real answer, not a bug -- show
      // what RouterOS/the network said rather than a generic failure.
      alert(err?.response?.data?.detail || "Couldn't reach this router.");
      refetch();
    } finally {
      setPollingDevice(null);
    }
  }

  async function handleDelete(device: Device) {
    if (
      !confirm(
        `Delete ${device.name} (${device.ip_address})? This also deletes its monitoring history and unlinks any ` +
          "customer services pointed at it. This can't be undone."
      )
    )
      return;
    try {
      await api.delete(`/devices/${device.id}/`);
      if (editingDevice?.id === device.id) setShowModal(false);
      refetch();
    } catch (err: any) {
      alert(err?.response?.data?.detail || "Couldn't delete this router.");
    }
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select className={filterSelectClass} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          <option value="">All types</option>
          <option value="router">Router</option>
          <option value="switch">Switch</option>
          <option value="olt">OLT</option>
          <option value="access_point">Access Point</option>
          <option value="server">Server</option>
          <option value="onu">ONU/CPE</option>
        </select>
        <select className={filterSelectClass} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          <option value="online">Online</option>
          <option value="offline">Offline</option>
          <option value="unknown">Unknown</option>
        </select>
        {(typeFilter || statusFilter) && (
          <button
            type="button"
            className={btnSecondary}
            onClick={() => {
              setTypeFilter("");
              setStatusFilter("");
            }}
          >
            Clear filters
          </button>
        )}
        <div className="ml-auto">
          <ColumnToggle columns={DEVICE_COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["name"]} />
        </div>
      </div>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Name</TH>
              {isVisible("type") && <TH>Type</TH>}
              {isVisible("ip_address") && <TH>IP Address</TH>}
              {isVisible("location") && <TH>Location</TH>}
              {isVisible("site") && <TH>Site</TH>}
              {isVisible("status") && <TH>Status</TH>}
              {isVisible("latency") && <TH>Latency</TH>}
              {isVisible("bandwidth") && <TH>Bandwidth (in/out)</TH>}
              {isVisible("partners") && <TH>Partners</TH>}
              <TH>API</TH>
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((d) => (
              <TR key={d.id} onClick={() => navigate(`/admin/networking/devices/${d.id}`)}>
                <TD className="font-medium">{d.name}</TD>
                {isVisible("type") && <TD className="capitalize">{d.device_type.replace("_", " ")}</TD>}
                {isVisible("ip_address") && <TD>{d.ip_address}</TD>}
                {isVisible("location") && <TD>{d.location}</TD>}
                {isVisible("site") && <TD>{d.site_name ?? "—"}</TD>}
                {isVisible("status") && <TD><StatusBadge status={d.status} /></TD>}
                {isVisible("latency") && (
                  <TD className="tabular-nums">{d.latest_reading?.latency_ms != null ? `${d.latest_reading.latency_ms} ms` : "—"}</TD>
                )}
                {isVisible("bandwidth") && (
                  <TD className="tabular-nums">
                    {d.latest_reading?.bandwidth_in_mbps != null
                      ? `${d.latest_reading.bandwidth_in_mbps} / ${d.latest_reading.bandwidth_out_mbps} Mbps`
                      : "—"}
                  </TD>
                )}
                {isVisible("partners") && (
                  <TD>{d.visible_partners.length === 0 ? "All" : `${d.visible_partners.length} of ${partners.length}`}</TD>
                )}
                <TD>{d.api_enabled ? "Enabled" : "—"}</TD>
                <TD>
                  <div className="flex gap-3">
                    {d.api_enabled && (
                      // Status/latency/bandwidth are written by the
                      // poll_mikrotik_devices cron, so a router configured
                      // a minute ago reads "Unknown" until the next run.
                      // This asks for a reading now rather than waiting.
                      <button
                        className="text-[var(--series-1)] hover:underline disabled:opacity-50"
                        disabled={pollingDevice === d.id}
                        title="Read status, CPU and bandwidth from this router now"
                        onClick={(e) => {
                          e.stopPropagation();
                          pollNow(d);
                        }}
                      >
                        {pollingDevice === d.id ? "Polling…" : "Poll now"}
                      </button>
                    )}
                    <button
                      className="text-[var(--series-1)] hover:underline"
                      onClick={(e) => {
                        e.stopPropagation();
                        openEdit(d);
                      }}
                    >
                      Edit
                    </button>
                    <button
                      className="text-red-600 hover:underline dark:text-red-400"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(d);
                      }}
                    >
                      Delete
                    </button>
                  </div>
                </TD>
              </TR>
            ))}
            {items.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]">No routers match the current filters.</TD>
              </TR>
            )}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title={editingDevice ? "Edit router" : "New router"} onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Name">
              <input className={inputClass} required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </FormField>
            <FormField label="Type">
              <select className={inputClass} value={form.device_type} onChange={(e) => setForm({ ...form, device_type: e.target.value as Device["device_type"] })}>
                <option value="router">Router</option>
                <option value="switch">Switch</option>
                <option value="olt">OLT</option>
                <option value="access_point">Access Point</option>
                <option value="server">Server</option>
                <option value="onu">ONU/CPE</option>
              </select>
            </FormField>
            <FormField label="IP address">
              <input className={inputClass} required value={form.ip_address} onChange={(e) => setForm({ ...form, ip_address: e.target.value })} />
            </FormField>
            <FormField label="Location">
              <input className={inputClass} value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} />
            </FormField>
            <FormField label="Site">
              <select
                className={inputClass}
                value={form.site ?? ""}
                onChange={(e) => setForm({ ...form, site: e.target.value ? Number(e.target.value) : null })}
              >
                <option value="">No site</option>
                {sites.map((s) => (
                  <option key={s.id} value={s.id}>{s.title}</option>
                ))}
              </select>
            </FormField>
            <FormField label="Vendor">
              <input className={inputClass} value={form.vendor} onChange={(e) => setForm({ ...form, vendor: e.target.value })} />
            </FormField>
            {partners.length > 0 && (
              <FormField label="Partners (which staff can see this router)">
                <PartnerMultiSelect
                  allPartners={partners}
                  selected={form.visible_partners ?? []}
                  onChange={(ids) => setForm({ ...form, visible_partners: ids })}
                />
              </FormField>
            )}

            <div className="mt-4 border-t border-[var(--border-hairline)] pt-4">
              <label className="flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
                <input
                  type="checkbox"
                  checked={!!form.api_enabled}
                  onChange={(e) => setForm({ ...form, api_enabled: e.target.checked })}
                />
                Enable Mikrotik RouterOS API
              </label>
              <p className="mb-3 mt-1 text-xs text-[var(--text-muted)]">
                Lets this platform pull real monitoring data, view/disconnect live PPP sessions, and push RADIUS
                config directly to this device instead of relying on simulated readings.
              </p>
              {form.api_enabled && (
                <>
                  <FormField label="API port">
                    <input
                      type="number"
                      className={inputClass}
                      value={form.api_port ?? 8728}
                      onChange={(e) => setForm({ ...form, api_port: Number(e.target.value) })}
                    />
                  </FormField>
                  <FormField label="API username">
                    <input
                      className={inputClass}
                      value={form.api_username ?? ""}
                      onChange={(e) => setForm({ ...form, api_username: e.target.value })}
                      autoComplete="off"
                      name="device-api-username"
                    />
                  </FormField>
                  <FormField label={`API password${editingDevice?.api_password_set ? " (set — leave blank to keep)" : ""}`}>
                    <input
                      type="password"
                      className={inputClass}
                      placeholder={editingDevice?.api_password_set ? "••••••••" : "Set a password"}
                      value={form.api_password ?? ""}
                      onChange={(e) => setForm({ ...form, api_password: e.target.value })}
                      autoComplete="new-password"
                      name="device-api-password"
                    />
                  </FormField>
                  <FormField label="WAN interface (for bandwidth polling)">
                    <input
                      className={inputClass}
                      placeholder="ether1"
                      value={form.api_wan_interface ?? ""}
                      onChange={(e) => setForm({ ...form, api_wan_interface: e.target.value })}
                    />
                  </FormField>
                  <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                    <input
                      type="checkbox"
                      checked={!!form.api_use_ssl}
                      onChange={(e) => setForm({ ...form, api_use_ssl: e.target.checked })}
                    />
                    Use API-SSL (port 8729)
                  </label>

                  <div className="mt-4 border-t border-[var(--border-hairline)] pt-4">
                    <p className="mb-2 text-sm font-semibold text-[var(--text-primary)]">Live API push</p>
                    <p className="mb-3 text-xs text-[var(--text-muted)]">
                      Everything below is pushed to this router the moment you click "Sync now" (or Save + Sync),
                      tagged so it can always be found and removed again. Build + test against a real/spare router
                      before relying on this for live customers.
                    </p>

                    <label className="mb-2 flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                      <input
                        type="checkbox"
                        checked={!!form.block_disabled_customers}
                        onChange={(e) => setForm({ ...form, block_disabled_customers: e.target.checked })}
                      />
                      Block disabled customers (address-list + firewall drop)
                    </label>

                    <label className="mb-2 flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                      <input
                        type="checkbox"
                        checked={!!form.enable_shaper}
                        onChange={(e) => setForm({ ...form, enable_shaper: e.target.checked })}
                      />
                      Enable Shaper (Simple Queue per active service)
                    </label>

                    <label className="mb-2 flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                      <input
                        type="checkbox"
                        checked={!!form.enable_wireless_access_list}
                        onChange={(e) => setForm({ ...form, enable_wireless_access_list: e.target.checked })}
                      />
                      Enable Wireless Access List (manage from the router's own detail page)
                    </label>

                    {form.enable_wireless_access_list && (
                      <>
                        <label className="mb-2 ml-6 flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                          <input
                            type="checkbox"
                            checked={!!form.enable_mpsk}
                            onChange={(e) => setForm({ ...form, enable_mpsk: e.target.checked })}
                          />
                          Allow MPSK (per-client WPA2 passphrase)
                        </label>
                        <FormField label="Wireless interface">
                          <input
                            className={inputClass}
                            placeholder="wlan1"
                            value={form.wireless_interface ?? ""}
                            onChange={(e) => setForm({ ...form, wireless_interface: e.target.value })}
                          />
                        </FormField>
                      </>
                    )}

                    {editingDevice && (
                      <div className="mt-3 flex flex-wrap gap-2">
                        <button
                          type="button"
                          className={btnSecondary}
                          disabled={!!syncBusy}
                          onClick={() => handleSyncBlockingRules(editingDevice)}
                        >
                          {syncBusy === "blocking" ? "Syncing…" : "Sync blocking rules"}
                        </button>
                        <button
                          type="button"
                          className={btnSecondary}
                          disabled={!!syncBusy}
                          onClick={() => handleSyncShaperQueues(editingDevice)}
                        >
                          {syncBusy === "shaper" ? "Syncing…" : "Sync shaper queues"}
                        </button>
                        <button
                          type="button"
                          className="rounded-md border border-red-300 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50 disabled:opacity-50 dark:border-red-800 dark:text-red-300 dark:hover:bg-red-950"
                          disabled={!!syncBusy}
                          onClick={() => handleDeleteAllRules(editingDevice)}
                        >
                          {syncBusy === "delete-all" ? "Clearing…" : "Delete all rules from router"}
                        </button>
                      </div>
                    )}
                    {syncMessage && <p className="mt-2 text-xs text-[var(--text-muted)]">{syncMessage}</p>}
                    {editingDevice && form.enable_wireless_access_list && (
                      <p className="mt-2 text-xs text-[var(--text-muted)]">
                        Manage individual Access List / MPSK entries from{" "}
                        <button
                          type="button"
                          className="text-[var(--series-1)] hover:underline"
                          onClick={() => navigate(`/admin/networking/devices/${editingDevice.id}`)}
                        >
                          this router's detail page
                        </button>
                        .
                      </p>
                    )}
                  </div>
                </>
              )}
            </div>

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>Cancel</button>
              <button type="submit" disabled={saving} className={btnPrimary}>{saving ? "Saving…" : editingDevice ? "Save changes" : "Create router"}</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sites -- physical tower/site locations hardware can be mounted at
// ---------------------------------------------------------------------------

const SITE_COLUMNS: ColumnDef[] = [
  { key: "title", label: "Title" },
  { key: "contact", label: "Contact" },
  { key: "phone", label: "Phone" },
  { key: "address", label: "Address" },
  { key: "partner", label: "Partner" },
  { key: "hardware", label: "Hardware" },
];

const EMPTY_SITE_FORM: Partial<NetworkSite> = {
  title: "",
  contact_person: "",
  phone: "",
  address: "",
  location: "",
  partners: [],
  notes: "",
};

function SitesTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const { items, loading, refetch } = useApiList<NetworkSite>("/network-sites/?page_size=200&ordering=title");
  const { items: partners } = useApiList<Partner>("/partners/?page_size=200&ordering=name");
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("network-sites", ["title"]);
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<NetworkSite | null>(null);
  const [form, setForm] = useState<Partial<NetworkSite>>(EMPTY_SITE_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    onRegisterNewAction({
      label: "+ Add site",
      onClick: () => {
        setEditing(null);
        setForm(EMPTY_SITE_FORM);
        setError("");
        setShowModal(true);
      },
    });
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function openEdit(site: NetworkSite) {
    setEditing(site);
    setForm({
      title: site.title,
      contact_person: site.contact_person,
      phone: site.phone,
      address: site.address,
      location: site.location,
      partners: site.partners,
      notes: site.notes,
    });
    setError("");
    setShowModal(true);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      if (editing) {
        await api.patch(`/network-sites/${editing.id}/`, form);
      } else {
        await api.post("/network-sites/", form);
      }
      setShowModal(false);
      refetch();
    } catch (err: any) {
      const data = err?.response?.data;
      const firstError = data && typeof data === "object" ? Object.values(data).flat()[0] : null;
      setError(typeof firstError === "string" ? firstError : "Could not save this site — please try again.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(site: NetworkSite) {
    if (
      !confirm(
        `Delete "${site.title}"? ${
          site.hardware_count > 0
            ? `Its ${site.hardware_count} device(s) won't be deleted -- they'll just lose their site assignment.`
            : "It has no devices assigned."
        }`
      )
    )
      return;
    try {
      await api.delete(`/network-sites/${site.id}/`);
      refetch();
    } catch (err: any) {
      alert(err?.response?.data?.detail || "Couldn't delete this site.");
    }
  }

  return (
    <div>
      <div className="mb-4 flex justify-end">
        <ColumnToggle columns={SITE_COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["title"]} />
      </div>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Title</TH>
              {isVisible("contact") && <TH>Contact</TH>}
              {isVisible("phone") && <TH>Phone</TH>}
              {isVisible("address") && <TH>Address</TH>}
              {isVisible("partner") && <TH>Partner</TH>}
              {isVisible("hardware") && <TH>Hardware</TH>}
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((s) => (
              <TR key={s.id}>
                <TD className="font-medium">{s.title}</TD>
                {isVisible("contact") && <TD>{s.contact_person || "—"}</TD>}
                {isVisible("phone") && <TD>{s.phone || "—"}</TD>}
                {isVisible("address") && <TD>{s.address || "—"}</TD>}
                {isVisible("partner") && <TD>{s.partner_names.length ? s.partner_names.join(", ") : "All"}</TD>}
                {isVisible("hardware") && <TD className="tabular-nums">{s.hardware_count}</TD>}
                <TD>
                  <div className="flex gap-3">
                    <button className="text-[var(--series-1)] hover:underline" onClick={() => openEdit(s)}>Edit</button>
                    <button className="text-red-600 hover:underline dark:text-red-400" onClick={() => handleDelete(s)}>Delete</button>
                  </div>
                </TD>
              </TR>
            ))}
            {items.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]">No sites yet.</TD>
              </TR>
            )}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title={editing ? `Edit ${editing.title}` : "New site"} onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Title">
              <input className={inputClass} required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </FormField>
            <FormField label="Contact person">
              <input className={inputClass} value={form.contact_person} onChange={(e) => setForm({ ...form, contact_person: e.target.value })} />
            </FormField>
            <FormField label="Phone">
              <input className={inputClass} value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
            </FormField>
            <FormField label="Address">
              <input className={inputClass} value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} />
            </FormField>
            <FormField label="Location (for filtering)">
              <input className={inputClass} value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} />
            </FormField>
            {partners.length > 0 && (
              <FormField label="Partners served from this site">
                {/* A tower routinely serves more than one reseller. Forcing a
                    choice between them meant the site list couldn't answer
                    "which sites does this partner have customers on", which
                    is the question it exists for. Same control and the same
                    empty-means-all convention as a router's partners. */}
                <PartnerMultiSelect
                  allPartners={partners}
                  selected={form.partners ?? []}
                  onChange={(ids) => setForm({ ...form, partners: ids })}
                />
              </FormField>
            )}
            <FormField label="Notes">
              <textarea className={inputClass} rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
            </FormField>

            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>Cancel</button>
              <button type="submit" disabled={saving} className={btnPrimary}>{saving ? "Saving…" : editing ? "Save changes" : "Create site"}</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// IP Pools
// ---------------------------------------------------------------------------

const POOL_COLUMNS: ColumnDef[] = [
  { key: "name", label: "Name" },
  { key: "category", label: "Category" },
  { key: "root_net", label: "RootNet" },
  { key: "network_type", label: "Network type" },
  { key: "cidr", label: "CIDR" },
  { key: "gateway", label: "Gateway" },
  { key: "type", label: "Type" },
  { key: "free_total", label: "Free / Total" },
  { key: "usage", label: "Usage" },
];

// Small inline usage bar (Splynx's "Used" column) -- a plain div rather
// than a chart component since it's just one static percentage per row.
function UsageBar({ pct }: { pct: number }) {
  return (
    <div className="h-2 w-24 overflow-hidden rounded-full bg-[var(--tint-hover)]">
      <div
        className="h-full rounded-full bg-[var(--series-1)]"
        style={{ width: `${Math.max(0, Math.min(100, pct))}%` }}
      />
    </div>
  );
}

const ADDRESS_COLUMNS: ColumnDef[] = [
  { key: "address", label: "Address" },
  { key: "status", label: "Status" },
  { key: "assigned_service", label: "Assigned service" },
];

function IPPoolsTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const [categoryFilter, setCategoryFilter] = useState<PoolCategory | "">("");
  const categoryParam = categoryFilter ? `&category=${categoryFilter}` : "";
  const { items, loading, refetch } = useApiList<IPPool>(`/ip-pools/?page_size=100${categoryParam}`);
  const [showModal, setShowModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState<IPPool | null>(null);
  const [formError, setFormError] = useState("");
  const [busyPool, setBusyPool] = useState<number | null>(null);
  const [form, setForm] = useState<Partial<IPPool>>({
    name: "", network_cidr: "", gateway: "", pool_type: "ipv4", category: "customer",
    network_type: "endnet", root_net: null,
  });
  const [selectedPool, setSelectedPool] = useState<number | null>(null);
  const [addresses, setAddresses] = useState<IPAddress[]>([]);
  const [addressStatusFilter, setAddressStatusFilter] = useState("");
  const { hidden: hiddenPoolCols, isVisible: isPoolColVisible, toggle: togglePoolCol } = useColumnVisibility("ip-pools", ["name"]);
  const { hidden: hiddenAddrCols, isVisible: isAddrColVisible, toggle: toggleAddrCol } = useColumnVisibility("ip-addresses", ["address"]);

  useEffect(() => {
    onRegisterNewAction({ label: "+ New pool", onClick: () => openCreate() });
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [addressReload, setAddressReload] = useState(0);

  useEffect(() => {
    if (selectedPool == null) return;
    const statusParam = addressStatusFilter ? `&status=${addressStatusFilter}` : "";
    api
      .get<{ results: IPAddress[] }>(`/ip-addresses/?pool=${selectedPool}&page_size=200${statusParam}`)
      .then((res) => setAddresses(res.data.results));
  }, [selectedPool, addressStatusFilter, addressReload]);

  function openCreate() {
    setEditing(null);
    setFormError("");
    setForm({
      name: "", network_cidr: "", gateway: "", pool_type: "ipv4", category: "customer",
      network_type: "endnet", root_net: null,
    });
    setShowModal(true);
  }

  function openEdit(pool: IPPool) {
    setEditing(pool);
    setFormError("");
    setForm({
      name: pool.name, network_cidr: pool.network_cidr, gateway: pool.gateway ?? "",
      pool_type: pool.pool_type, category: pool.category, network_type: pool.network_type,
      root_net: pool.root_net, description: pool.description,
    });
    setShowModal(true);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError("");
    setSaving(true);
    try {
      const payload = { ...form, gateway: form.gateway || null };
      if (editing) {
        await api.patch(`/ip-pools/${editing.id}/`, payload);
      } else {
        await api.post("/ip-pools/", payload);
      }
      setShowModal(false);
      refetch();
    } catch (err: any) {
      const data = err?.response?.data;
      const message = data && typeof data === "object" ? Object.values(data).flat().join(" ") : null;
      setFormError(message || "Couldn't save this pool.");
    } finally {
      setSaving(false);
    }
  }

  // Creating a pool only records its CIDR -- the individual addresses have
  // to be generated before anything can be handed out of it. A pool sitting
  // at 0 / 0 authenticates customers and then gives them no address at all,
  // so this is the step that makes a new pool actually usable.
  async function handleGenerate(pool: IPPool) {
    if (
      !confirm(
        `Generate the addresses in ${pool.network_cidr} for "${pool.name}"?\n\n` +
          (pool.gateway ? `The gateway ${pool.gateway} is skipped. ` : "") +
          "Existing addresses are left alone, so this is safe to re-run."
      )
    )
      return;
    setBusyPool(pool.id);
    try {
      const res = await api.post<{
        created: number; already_in_this_pool: number; in_another_pool: number; total_in_pool: number;
      }>(`/ip-pools/${pool.id}/generate-addresses/`, {});
      const d = res.data;
      const notes = [`${d.created} address(es) created`];
      if (d.already_in_this_pool) notes.push(`${d.already_in_this_pool} already here`);
      if (d.in_another_pool) notes.push(`${d.in_another_pool} skipped — already in another pool (overlapping ranges?)`);
      alert(`${notes.join("\n")}\n\nThis pool now holds ${d.total_in_pool}.`);
      refetch();
      setAddressReload((n) => n + 1);
    } catch (err: any) {
      const data = err?.response?.data;
      const message = data && typeof data === "object" ? Object.values(data).flat().join(" ") : null;
      alert(message || "Couldn't generate addresses for this pool.");
    } finally {
      setBusyPool(null);
    }
  }

  async function handleClearFree(pool: IPPool) {
    if (
      !confirm(
        `Delete the unused addresses in "${pool.name}"?\n\n` +
          "Assigned and reserved addresses are kept — a customer's live address is never removed. " +
          "Use this to shrink a pool or undo a range generated too wide."
      )
    )
      return;
    setBusyPool(pool.id);
    try {
      const res = await api.post<{ deleted: number; kept_in_use: number }>(
        `/ip-pools/${pool.id}/clear-free-addresses/`, {}
      );
      alert(`${res.data.deleted} removed, ${res.data.kept_in_use} kept because they're in use.`);
      refetch();
      setAddressReload((n) => n + 1);
    } catch (err: any) {
      alert(err?.response?.data?.detail || "Couldn't clear addresses for this pool.");
    } finally {
      setBusyPool(null);
    }
  }

  async function handleDelete(pool: IPPool) {
    if (
      !confirm(
        `Delete the pool "${pool.name}" (${pool.network_cidr})? This also deletes all ${pool.total_count} of its ` +
          "addresses. This can't be undone."
      )
    )
      return;
    try {
      await api.delete(`/ip-pools/${pool.id}/`);
      if (selectedPool === pool.id) setSelectedPool(null);
      refetch();
    } catch (err: any) {
      const data = err?.response?.data;
      const message = data && typeof data === "object" ? Object.values(data).flat().join(" ") : null;
      alert(message || "Couldn't delete this pool.");
    }
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <select
            className={filterSelectClass}
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value as PoolCategory | "")}
          >
            <option value="">All categories</option>
            <option value="customer">Customer IP Pool</option>
            <option value="network">Net IP Pool</option>
            <option value="walled_garden">Walled Garden (no internet)</option>
          </select>
          {categoryFilter && (
            <button type="button" className={btnSecondary} onClick={() => setCategoryFilter("")}>
              Clear
            </button>
          )}
        </div>
        <ColumnToggle columns={POOL_COLUMNS} hidden={hiddenPoolCols} onToggle={togglePoolCol} alwaysVisible={["name"]} />
      </div>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Name</TH>
              {isPoolColVisible("category") && <TH>Category</TH>}
              {isPoolColVisible("root_net") && <TH>RootNet</TH>}
              {isPoolColVisible("network_type") && <TH>Network type</TH>}
              {isPoolColVisible("cidr") && <TH>CIDR</TH>}
              {isPoolColVisible("gateway") && <TH>Gateway</TH>}
              {isPoolColVisible("type") && <TH>Type</TH>}
              {isPoolColVisible("free_total") && <TH>Free / Total</TH>}
              {isPoolColVisible("usage") && <TH>Usage</TH>}
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((p) => (
              <TR key={p.id}>
                <TD className="font-medium">{p.name}</TD>
                {isPoolColVisible("category") && <TD>{POOL_CATEGORY_LABELS[p.category]}</TD>}
                {isPoolColVisible("root_net") && <TD>{p.root_net_name ?? "None"}</TD>}
                {isPoolColVisible("network_type") && <TD>{NETWORK_TYPE_LABELS[p.network_type]}</TD>}
                {isPoolColVisible("cidr") && <TD>{p.network_cidr}</TD>}
                {isPoolColVisible("gateway") && <TD>{p.gateway ?? "—"}</TD>}
                {isPoolColVisible("type") && <TD className="uppercase">{p.pool_type}</TD>}
                {isPoolColVisible("free_total") && (
                  <TD className="tabular-nums">
                    {p.free_count} / {p.total_count}
                    {p.total_count === 0 && (
                      // An empty pool is the quiet failure mode: logins are
                      // accepted and then handed no address at all.
                      <span
                        className="ml-2 whitespace-nowrap rounded bg-[var(--tint-hover)] px-1.5 py-0.5 text-xs font-normal text-[var(--status-critical)]"
                        title="This pool has no addresses yet — use Generate addresses, or nothing can be handed out of it"
                      >
                        not generated
                      </span>
                    )}
                  </TD>
                )}
                {isPoolColVisible("usage") && (
                  <TD>
                    <div className="flex items-center gap-2">
                      <UsageBar pct={p.used_pct} />
                      <span className="text-xs tabular-nums text-[var(--text-muted)]">{p.used_pct}%</span>
                    </div>
                  </TD>
                )}
                <TD>
                  <div className="flex items-center gap-3">
                    <button className="text-[var(--series-1)] hover:underline" onClick={() => setSelectedPool(p.id)}>
                      View addresses
                    </button>
                    <button className="text-[var(--series-1)] hover:underline" onClick={() => openEdit(p)}>
                      Edit
                    </button>
                    <button
                      className="text-[var(--series-1)] hover:underline disabled:opacity-50"
                      disabled={busyPool === p.id}
                      onClick={() => handleGenerate(p)}
                      title="Create the individual addresses in this pool's range so they can be handed out"
                    >
                      {busyPool === p.id ? "Working…" : "Generate addresses"}
                    </button>
                    {p.total_count > 0 && (
                      <button
                        className="text-[var(--text-muted)] hover:underline disabled:opacity-50"
                        disabled={busyPool === p.id}
                        onClick={() => handleClearFree(p)}
                        title="Delete the unused addresses, keeping any that are assigned or reserved"
                      >
                        Clear unused
                      </button>
                    )}
                    <button
                      className="text-red-600 hover:underline dark:text-red-400"
                      onClick={() => handleDelete(p)}
                    >
                      Delete
                    </button>
                  </div>
                </TD>
              </TR>
            ))}
            {items.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]">No IP pools yet.</TD>
              </TR>
            )}
          </tbody>
        </Table>
      )}

      {selectedPool != null && (
        <div className="mt-6">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold">Addresses in pool</h2>
            <div className="flex items-center gap-2">
              <select
                className={filterSelectClass}
                value={addressStatusFilter}
                onChange={(e) => setAddressStatusFilter(e.target.value)}
              >
                <option value="">All statuses</option>
                <option value="free">Free</option>
                <option value="assigned">Assigned</option>
                <option value="reserved">Reserved</option>
              </select>
              {addressStatusFilter && (
                <button type="button" className={btnSecondary} onClick={() => setAddressStatusFilter("")}>
                  Clear
                </button>
              )}
              <ColumnToggle columns={ADDRESS_COLUMNS} hidden={hiddenAddrCols} onToggle={toggleAddrCol} alwaysVisible={["address"]} />
            </div>
          </div>
          <Table>
            <THead>
              <tr>
                <TH>Address</TH>
                {isAddrColVisible("status") && <TH>Status</TH>}
                {isAddrColVisible("assigned_service") && <TH>Assigned service</TH>}
              </tr>
            </THead>
            <tbody>
              {addresses.map((a) => (
                <TR key={a.id}>
                  <TD>{a.address}</TD>
                  {isAddrColVisible("status") && <TD><StatusBadge status={a.status} /></TD>}
                  {isAddrColVisible("assigned_service") && <TD>{a.assigned_service ?? "—"}</TD>}
                </TR>
              ))}
            </tbody>
          </Table>
        </div>
      )}

      {showModal && (
        <Modal title={editing ? `Edit IP pool — ${editing.name}` : "New IP pool"} onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Name">
              <input className={inputClass} required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </FormField>
            <FormField label="Category">
              <select className={inputClass} value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value as PoolCategory })}>
                <option value="customer">Customer IP Pool</option>
                <option value="network">Net IP Pool (RADIUS / OVPN)</option>
                <option value="walled_garden">Walled Garden (suspended PPPoE customers, no internet)</option>
              </select>
            </FormField>
            <FormField label="Network CIDR">
              <input
                className={inputClass}
                required
                placeholder="10.20.0.0/24"
                value={form.network_cidr}
                disabled={!!editing && editing.total_count > 0}
                onChange={(e) => setForm({ ...form, network_cidr: e.target.value })}
              />
              {!!editing && editing.total_count > 0 && (
                <p className="mt-1 text-xs text-[var(--text-muted)]">
                  Locked — this pool holds {editing.total_count} generated address(es). Changing the range would
                  leave them outside it. Clear the unused ones (and release any assigned) first, then edit.
                </p>
              )}
            </FormField>
            <FormField label="Gateway">
              <input className={inputClass} placeholder="10.20.0.1" value={form.gateway ?? ""} onChange={(e) => setForm({ ...form, gateway: e.target.value })} />
            </FormField>
            <FormField label="Type">
              <select className={inputClass} value={form.pool_type} onChange={(e) => setForm({ ...form, pool_type: e.target.value as IPPool["pool_type"] })}>
                <option value="ipv4">IPv4</option>
                <option value="ipv6">IPv6</option>
              </select>
            </FormField>
            <FormField label="Network type">
              <select
                className={inputClass}
                value={form.network_type}
                onChange={(e) => setForm({ ...form, network_type: e.target.value as IPPool["network_type"] })}
              >
                <option value="endnet">EndNet</option>
                <option value="rootnet">RootNet</option>
              </select>
            </FormField>
            {items.filter((p) => p.id !== editing?.id).length > 0 && (
              <FormField label="RootNet (optional parent network)">
                <select
                  className={inputClass}
                  value={form.root_net ?? ""}
                  onChange={(e) => setForm({ ...form, root_net: e.target.value ? Number(e.target.value) : null })}
                >
                  <option value="">None</option>
                  {/* A pool can't be its own parent -- the backend rejects it,
                      so don't offer it. */}
                  {items.filter((p) => p.id !== editing?.id).map((p) => (
                    <option key={p.id} value={p.id}>{p.name} ({p.network_cidr})</option>
                  ))}
                </select>
              </FormField>
            )}
            {formError && <p className="mb-3 text-sm text-[var(--status-critical)]">{formError}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>Cancel</button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : editing ? "Save changes" : "Create pool"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// OpenVPN (outbound OpenVPN client tunnels this platform's own VPS dials
// out on). Tab key stays "vpn-clients" and the API stays
// /ovpn-client-connections/ -- only the visible label changed.
// ---------------------------------------------------------------------------

const VPN_COLUMNS: ColumnDef[] = [
  { key: "name", label: "Connection name" },
  { key: "status", label: "Status" },
  { key: "comment", label: "Comment" },
  { key: "remote", label: "Remote" },
  { key: "username", label: "Username" },
  { key: "enabled", label: "Enabled" },
];

// How often this tab re-pings each connection's remote address on its own,
// in addition to the manual "Refresh status" button -- a live network check,
// not a persisted field, so it's always at most this stale. (Was shared with
// the RADIUS Clients tab before that moved to Configs.)
const VPN_STATUS_REFRESH_MS = 45_000;

const emptyVpnForm: Partial<OvpnClientConnection> & { password: string } = {
  name: "",
  comment: "",
  remote_ip: "",
  remote_port: 1194,
  username: "",
  password: "",
  routes: "",
  is_enabled: true,
};

function VpnClientsTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const { items, loading, refetch } = useApiList<OvpnClientConnection>("/ovpn-client-connections/?page_size=100");
  const [showModal, setShowModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState<OvpnClientConnection | null>(null);
  const [form, setForm] = useState(emptyVpnForm);
  const [formError, setFormError] = useState("");
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("vpn-client-connections", ["name"]);

  // Live online/offline status -- a ping run right now against every
  // connection's remote_ip, not a persisted field. See
  // OvpnClientConnectionPingStatus and the backend's ping_status action.
  const [statusById, setStatusById] = useState<Record<number, "online" | "offline">>({});
  const [statusChecking, setStatusChecking] = useState(false);

  async function refreshStatuses() {
    setStatusChecking(true);
    try {
      const res = await api.get<OvpnClientConnectionPingStatus[]>("/ovpn-client-connections/ping-status/");
      setStatusById((prev) => {
        const next = { ...prev };
        res.data.forEach((entry) => {
          next[entry.id] = entry.status;
        });
        return next;
      });
    } catch {
      // Best-effort status indicator -- a failed check just leaves the
      // last known status (or "Checking…") in place.
    } finally {
      setStatusChecking(false);
    }
  }

  useEffect(() => {
    refreshStatuses();
    const interval = setInterval(refreshStatuses, VPN_STATUS_REFRESH_MS);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    onRegisterNewAction({ label: "+ Add OpenVPN client", onClick: () => openCreate() });
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function openCreate() {
    setEditing(null);
    setForm(emptyVpnForm);
    setFormError("");
    setShowModal(true);
  }

  function openEdit(conn: OvpnClientConnection) {
    setEditing(conn);
    setForm({
      name: conn.name,
      comment: conn.comment,
      remote_ip: conn.remote_ip,
      remote_port: conn.remote_port,
      username: conn.username,
      password: "",
      routes: conn.routes,
      is_enabled: conn.is_enabled,
    });
    setFormError("");
    setShowModal(true);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setFormError("");
    try {
      if (editing) {
        await api.patch(`/ovpn-client-connections/${editing.id}/`, form);
      } else {
        await api.post("/ovpn-client-connections/", form);
      }
      setShowModal(false);
      refetch();
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setFormError(typeof firstError === "string" ? firstError : "Could not save this connection — please try again.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(conn: OvpnClientConnection) {
    if (
      !confirm(
        `Delete the "${conn.name}" VPN client connection? This only removes the record here — if it's already ` +
          "installed as a systemd service on the VPS, you'll need to remove that separately. This can't be undone."
      )
    )
      return;
    try {
      await api.delete(`/ovpn-client-connections/${conn.id}/`);
      if (editing?.id === conn.id) setShowModal(false);
      refetch();
    } catch (err: any) {
      alert(err?.response?.data?.detail || "Couldn't delete this connection.");
    }
  }

  async function handleDuplicate(conn: OvpnClientConnection) {
    try {
      await api.post(`/ovpn-client-connections/${conn.id}/duplicate/`);
      refetch();
    } catch (err: any) {
      alert(err?.response?.data?.detail || "Couldn't duplicate this connection.");
    }
  }

  async function handleDownloadConfig(conn: OvpnClientConnection) {
    try {
      const res = await api.get(`/ovpn-client-connections/${conn.id}/config/`, { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement("a");
      link.href = url;
      link.download = `${conn.name}.conf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch {
      alert("Couldn't download this connection's config.");
    }
  }

  return (
    <div>
      <div className="mb-3 rounded-md border border-[var(--border-hairline)] bg-[var(--tint-hover)] p-3 text-xs text-[var(--text-secondary)]">
        Outbound OpenVPN client tunnels this platform's own VPS dials out on to reach a router's private management
        network. This only stores the connection's config;
        installing it as a real tunnel on the VPS (systemd's <code className="font-mono">openvpn-client@&lt;name&gt;</code>{" "}
        service) is still a manual step — use "Download config" per connection for the file to install there. The
        Status column is a live ping to the remote address (auto-refreshed every 45s), not confirmation the tunnel
        itself is actually up.
      </div>
      <div className="mb-4 flex items-center justify-end gap-3">
        <button
          type="button"
          className="text-sm text-[var(--series-1)] hover:underline disabled:opacity-50"
          onClick={() => refreshStatuses()}
          disabled={statusChecking}
        >
          {statusChecking ? "Checking status…" : "Refresh status"}
        </button>
        <ColumnToggle columns={VPN_COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["name"]} />
      </div>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Connection name</TH>
              {isVisible("status") && <TH>Status</TH>}
              {isVisible("comment") && <TH>Comment</TH>}
              {isVisible("remote") && <TH>Remote</TH>}
              {isVisible("username") && <TH>Username</TH>}
              {isVisible("enabled") && <TH>Enabled</TH>}
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((c) => (
              <TR key={c.id}>
                <TD className="font-medium">{c.name}</TD>
                {isVisible("status") && (
                  <TD>
                    {statusById[c.id] ? <StatusBadge status={statusById[c.id]} /> : <StatusBadge status="unknown" />}
                  </TD>
                )}
                {isVisible("comment") && <TD>{c.comment || "—"}</TD>}
                {isVisible("remote") && (
                  <TD>
                    {c.remote_ip}:{c.remote_port}
                  </TD>
                )}
                {isVisible("username") && <TD>{c.username || "—"}</TD>}
                {isVisible("enabled") && <TD>{c.is_enabled ? "Yes" : "No"}</TD>}
                <TD>
                  <div className="flex items-center gap-3">
                    <button className="text-[var(--series-1)] hover:underline" onClick={() => openEdit(c)}>
                      Edit
                    </button>
                    <button className="text-[var(--series-1)] hover:underline" onClick={() => handleDuplicate(c)}>
                      Duplicate
                    </button>
                    <button className="text-[var(--series-1)] hover:underline" onClick={() => handleDownloadConfig(c)}>
                      Download config
                    </button>
                    <button
                      className="text-red-600 hover:underline dark:text-red-400"
                      onClick={() => handleDelete(c)}
                    >
                      Delete
                    </button>
                  </div>
                </TD>
              </TR>
            ))}
            {items.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]">No VPN client connections configured yet.</TD>
              </TR>
            )}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title={editing ? "Edit OpenVPN client" : "Add OpenVPN client"} onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit} autoComplete="off">
            <FormField label="Enabled">
              <select
                className={inputClass}
                value={form.is_enabled ? "yes" : "no"}
                onChange={(e) => setForm({ ...form, is_enabled: e.target.value === "yes" })}
              >
                <option value="yes">Yes</option>
                <option value="no">No</option>
              </select>
            </FormField>
            <FormField label="Connection name">
              <input
                className={inputClass}
                required
                placeholder="skybre"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </FormField>
            <FormField label="Comment">
              <textarea
                className={inputClass}
                rows={2}
                placeholder="Skybre Network"
                value={form.comment}
                onChange={(e) => setForm({ ...form, comment: e.target.value })}
              />
            </FormField>
            <FormField label="Remote IP / hostname">
              <input
                className={inputClass}
                required
                placeholder="10.250.32.2"
                value={form.remote_ip}
                onChange={(e) => setForm({ ...form, remote_ip: e.target.value })}
              />
            </FormField>
            <FormField label="Remote port">
              <input
                type="number"
                className={inputClass}
                value={form.remote_port}
                onChange={(e) => setForm({ ...form, remote_port: Number(e.target.value) })}
              />
            </FormField>
            <FormField label="Username">
              <input
                className={inputClass}
                placeholder="e.g. skybre-mgmt"
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
                // This looks like a login form to browsers (a plain text
                // field right next to a password field), which prompts
                // them to offer saving/autofilling it -- and elsewhere,
                // autofilling that saved value into unrelated fields on
                // the site. autoComplete="off" tells the browser this
                // isn't a login to remember. Not a staff account, just a
                // remote VPN server's own username -- nothing to save.
                autoComplete="off"
                name="vpn-client-remote-username"
              />
            </FormField>
            <FormField label={`Password${editing?.password_set ? " (set — leave blank to keep)" : ""}`}>
              <input
                type="password"
                className={inputClass}
                placeholder={editing?.password_set ? "••••••••" : "Set a password"}
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                // "new-password" (not "off", which Chrome ignores on
                // password fields specifically) is what actually stops
                // Chrome from offering to save this as a login and from
                // suggesting a previously-saved password here.
                autoComplete="new-password"
                name="vpn-client-remote-password"
              />
            </FormField>
            <FormField label="Routes (one per line: network netmask gateway)">
              <textarea
                className={inputClass}
                rows={4}
                placeholder={"10.0.0.0 255.0.0.0 10.250.32.2\n172.16.0.0 255.255.0.0 10.250.32.2"}
                value={form.routes}
                onChange={(e) => setForm({ ...form, routes: e.target.value })}
              />
            </FormField>
            {formError && <p className="mb-3 text-sm text-[var(--status-critical)]">{formError}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Live Sessions (RADIUS accounting)
// ---------------------------------------------------------------------------

const SESSION_COLUMNS: ColumnDef[] = [
  { key: "username", label: "User" },
  { key: "realm", label: "Realm" },
  { key: "framedipaddress", label: "Assigned IP" },
  { key: "callingstationid", label: "Client MAC/ID" },
  { key: "acctstarttime", label: "Start" },
  { key: "duration", label: "Duration" },
  { key: "traffic", label: "In / Out" },
  { key: "status", label: "Status" },
];

function formatBytes(bytes: number | null) {
  if (bytes == null) return "—";
  const mb = bytes / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(1)} MB`;
}

function formatDuration(seconds: number | null) {
  if (seconds == null) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function LiveSessionsTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const [activeOnly, setActiveOnly] = useState(true);
  const [realmFilter, setRealmFilter] = useState("");
  const { items: nasClients } = useApiList<RadiusNasClient>("/radius-nas-clients/?page_size=200");
  const realms = Array.from(new Set(nasClients.map((c) => c.realm).filter((r): r is string => !!r))).sort();
  const { items, loading, refetch } = useApiList<RadAcctSession>(
    `/radius-sessions/?page_size=100${activeOnly ? "&active_only=true" : ""}${realmFilter ? `&realm=${encodeURIComponent(realmFilter)}` : ""}`
  );
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("radius-sessions", ["username"]);

  useEffect(() => {
    onRegisterNewAction(null);
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <select className={filterSelectClass} value={activeOnly ? "active" : "all"} onChange={(e) => setActiveOnly(e.target.value === "active")}>
            <option value="active">Currently connected only</option>
            <option value="all">All sessions (history)</option>
          </select>
          {realms.length > 0 && (
            <select className={filterSelectClass} value={realmFilter} onChange={(e) => setRealmFilter(e.target.value)}>
              <option value="">All realms</option>
              {realms.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          )}
          <button type="button" className={btnSecondary} onClick={() => refetch()}>
            Refresh
          </button>
        </div>
        <ColumnToggle columns={SESSION_COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["username"]} />
      </div>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>User</TH>
              {isVisible("realm") && <TH>Realm</TH>}
              {isVisible("framedipaddress") && <TH>Assigned IP</TH>}
              {isVisible("callingstationid") && <TH>Client MAC/ID</TH>}
              {isVisible("acctstarttime") && <TH>Start</TH>}
              {isVisible("duration") && <TH>Duration</TH>}
              {isVisible("traffic") && <TH>In / Out</TH>}
              {isVisible("status") && <TH>Status</TH>}
            </tr>
          </THead>
          <tbody>
            {items.map((s) => (
              <TR key={s.radacctid}>
                <TD className="font-medium">{s.username ?? "—"}</TD>
                {isVisible("realm") && <TD>{s.realm ?? "—"}</TD>}
                {isVisible("framedipaddress") && <TD>{s.framedipaddress ?? "—"}</TD>}
                {isVisible("callingstationid") && <TD>{s.callingstationid ?? "—"}</TD>}
                {isVisible("acctstarttime") && <TD>{s.acctstarttime ? new Date(s.acctstarttime).toLocaleString() : "—"}</TD>}
                {isVisible("duration") && <TD>{formatDuration(s.duration_seconds ?? s.acctsessiontime)}</TD>}
                {isVisible("traffic") && (
                  <TD className="tabular-nums">
                    {/* Router counters when a live reading exists -- they are
                        current to the second. The accounting counters lag by
                        the NAS's interim interval, so a session minutes old
                        otherwise reads 0.0 MB / 0.0 MB. */}
                    {formatBytes(s.live_input_octets ?? s.acctinputoctets)} /{" "}
                    {formatBytes(s.live_output_octets ?? s.acctoutputoctets)}
                    {s.live_input_octets == null && s.acctstoptime == null && (
                      <span className="ml-1 text-xs text-[var(--text-muted)]" title="The router reports byte counters on its accounting interval, which is every 5 minutes on this NAS. A session younger than that reads zero until the first update arrives.">
                        (awaiting first update)
                      </span>
                    )}
                  </TD>
                )}
                {isVisible("status") && (
                  <TD>
                    {s.is_active ? (
                      <StatusBadge status="online" />
                    ) : (
                      <span className="text-[var(--text-muted)]">Ended{s.acctterminatecause ? ` (${s.acctterminatecause})` : ""}</span>
                    )}
                  </TD>
                )}
              </TR>
            ))}
            {items.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]">
                  {activeOnly ? "No customers currently connected." : "No session history yet."}
                </TD>
              </TR>
            )}
          </tbody>
        </Table>
      )}
    </div>
  );
}

