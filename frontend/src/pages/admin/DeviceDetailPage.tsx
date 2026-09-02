import { useEffect, useState, type FormEvent } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from "recharts";
import { api } from "../../api/client";
import { PageHeader } from "../../components/PageHeader";
import { StatusBadge } from "../../components/StatusBadge";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { palette } from "../../palette";
import type {
  Device, MonitoringReading, MikrotikTestConnectionResult, MikrotikPppSession, ConnectionRule,
  WirelessAccessListEntry,
} from "../../types";

const EMPTY_RULE_FORM = { title: "", speed_down_kbps: 0, speed_up_kbps: 0, guaranteed_pct: 0 };

// One customer line that physically connects through this device
// (billing.Service.access_device). Shaped by the connected-customers
// action on DeviceViewSet.
type ConnectedCustomer = {
  service_id: number;
  customer_id: number;
  customer_name: string;
  customer_phone: string;
  customer_status: string;
  tariff_name: string;
  service_status: string;
  access_detail: string;
};
const EMPTY_WIRELESS_FORM = { mac_address: "", comment: "", passphrase: "" };

export function DeviceDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [device, setDevice] = useState<Device | null>(null);
  const [readings, setReadings] = useState<MonitoringReading[]>([]);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<MikrotikTestConnectionResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [polling, setPolling] = useState(false);
  const [pollError, setPollError] = useState<string | null>(null);
  const [sessions, setSessions] = useState<MikrotikPppSession[] | null>(null);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [sessionsError, setSessionsError] = useState<string | null>(null);

  // -- Connection rules (per-router speed-shaping profiles) -- data model
  // + CRUD only; nothing here is pushed to the router's live shaper yet.
  const [rules, setRules] = useState<ConnectionRule[]>([]);
  const [showRuleModal, setShowRuleModal] = useState(false);
  const [ruleForm, setRuleForm] = useState(EMPTY_RULE_FORM);
  const [ruleSaving, setRuleSaving] = useState(false);
  const [ruleError, setRuleError] = useState("");

  // -- Wireless Access List / MPSK -- live RouterOS push, only shown when
  // the router has Device.enable_wireless_access_list on.
  const [wirelessEntries, setWirelessEntries] = useState<WirelessAccessListEntry[] | null>(null);
  const [wirelessLoading, setWirelessLoading] = useState(false);
  const [wirelessError, setWirelessError] = useState<string | null>(null);
  const [showWirelessModal, setShowWirelessModal] = useState(false);
  const [wirelessForm, setWirelessForm] = useState(EMPTY_WIRELESS_FORM);
  const [wirelessSaving, setWirelessSaving] = useState(false);
  // Who connects through this box. Loaded with the page rather than behind
  // a button, because it is the answer to the question that brings anyone
  // to this screen during an outage.
  const [connected, setConnected] = useState<ConnectedCustomer[] | null>(null);
  const [connectedLoading, setConnectedLoading] = useState(false);
  // Pushing the static simple queues. Lives on THIS page now: it used to
  // be buried in a modal on the Networking list, while the only hint
  // pointing at it was down in the Connection rules panel here -- so you
  // had to already know where it was to find it.
  const [syncingShaper, setSyncingShaper] = useState(false);
  const [shaperResult, setShaperResult] = useState("");

  function loadDevice() {
    if (!id) return;
    api.get<Device>(`/devices/${id}/`).then((res) => setDevice(res.data));
  }

  function loadConnected() {
    if (!id) return;
    setConnectedLoading(true);
    api
      .get<{ count: number; results: ConnectedCustomer[] }>(`/devices/${id}/connected-customers/`)
      .then((res) => setConnected(res.data.results))
      .catch(() => setConnected([]))
      .finally(() => setConnectedLoading(false));
  }

  function loadReadings() {
    if (!id) return;
    api.get<{ results: MonitoringReading[] }>(`/monitoring-readings/?device=${id}&page_size=200`).then((res) =>
      setReadings([...res.data.results].reverse())
    );
  }

  function loadRules() {
    if (!id) return;
    api.get<{ results: ConnectionRule[] }>(`/connection-rules/?device=${id}&page_size=200`).then((res) =>
      setRules(res.data.results)
    );
  }

  useEffect(() => {
    loadDevice();
    loadReadings();
    loadRules();
    loadConnected();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function handleAddRule(e: FormEvent) {
    e.preventDefault();
    if (!id) return;
    setRuleError("");
    setRuleSaving(true);
    try {
      await api.post("/connection-rules/", { ...ruleForm, device: Number(id) });
      setShowRuleModal(false);
      setRuleForm(EMPTY_RULE_FORM);
      loadRules();
    } catch (err: any) {
      const data = err?.response?.data;
      const firstError = data && typeof data === "object" ? Object.values(data).flat()[0] : null;
      setRuleError(typeof firstError === "string" ? firstError : "Could not save this rule — please try again.");
    } finally {
      setRuleSaving(false);
    }
  }

  async function handleDeleteRule(rule: ConnectionRule) {
    if (!confirm(`Delete the connection rule "${rule.title}"?`)) return;
    try {
      await api.delete(`/connection-rules/${rule.id}/`);
      loadRules();
    } catch {
      alert("Couldn't delete this rule.");
    }
  }

  async function handleSyncShaper() {
    if (!id) return;
    setSyncingShaper(true);
    setShaperResult("");
    try {
      const res = await api.post<{ entries?: unknown[] } | unknown[]>(
        `/devices/${id}/sync-shaper-queues/`
      );
      const entries = Array.isArray(res.data) ? res.data : (res.data as { entries?: unknown[] })?.entries;
      const count = Array.isArray(entries) ? entries.length : 0;
      setShaperResult(
        count
          ? `Pushed ${count} queue${count === 1 ? "" : "s"} to the router.`
          : "Shaper is off for this router, so its managed queues were removed."
      );
      loadDevice();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setShaperResult(detail || "Couldn't reach the router to push the queues.");
    } finally {
      setSyncingShaper(false);
    }
  }

  async function handleTestConnection() {
    setTesting(true);
    setTestError(null);
    setTestResult(null);
    try {
      const res = await api.post<MikrotikTestConnectionResult>(`/devices/${id}/test-connection/`);
      setTestResult(res.data);
    } catch (err: any) {
      setTestError(err?.response?.data?.detail ?? "Couldn't reach this device's Mikrotik API.");
    } finally {
      setTesting(false);
    }
  }

  async function handlePollNow() {
    setPolling(true);
    setPollError(null);
    try {
      await api.post(`/devices/${id}/poll-now/`);
      loadDevice();
      loadReadings();
    } catch (err: any) {
      setPollError(err?.response?.data?.detail ?? "Couldn't poll this device.");
    } finally {
      setPolling(false);
    }
  }

  async function handleDeleteDevice() {
    if (!device) return;
    if (
      !confirm(
        `Delete ${device.name} (${device.ip_address})? This also deletes its monitoring history and unlinks any ` +
          "customer services pointed at it. This can't be undone."
      )
    )
      return;
    try {
      await api.delete(`/devices/${device.id}/`);
      navigate("/admin/networking");
    } catch (err: any) {
      alert(err?.response?.data?.detail || "Couldn't delete this router.");
    }
  }

  async function loadSessions() {
    setSessionsLoading(true);
    setSessionsError(null);
    try {
      const res = await api.get<MikrotikPppSession[]>(`/devices/${id}/live-sessions/`);
      setSessions(res.data);
    } catch (err: any) {
      setSessionsError(err?.response?.data?.detail ?? "Couldn't read live sessions from this device.");
    } finally {
      setSessionsLoading(false);
    }
  }

  async function handleDisconnect(sessionId: string) {
    try {
      await api.post(`/devices/${id}/disconnect-session/`, { session_id: sessionId });
      loadSessions();
    } catch (err: any) {
      setSessionsError(err?.response?.data?.detail ?? "Couldn't disconnect that session.");
    }
  }

  async function loadWirelessEntries() {
    setWirelessLoading(true);
    setWirelessError(null);
    try {
      const res = await api.get<WirelessAccessListEntry[]>(`/devices/${id}/wireless-access-list/`);
      setWirelessEntries(res.data);
    } catch (err: any) {
      setWirelessError(err?.response?.data?.detail ?? "Couldn't read the wireless Access List from this router.");
    } finally {
      setWirelessLoading(false);
    }
  }

  async function handleAddWirelessEntry(e: FormEvent) {
    e.preventDefault();
    setWirelessSaving(true);
    setWirelessError(null);
    try {
      await api.post(`/devices/${id}/wireless-access-list/`, {
        mac_address: wirelessForm.mac_address,
        comment: wirelessForm.comment,
        passphrase: wirelessForm.passphrase || undefined,
      });
      setShowWirelessModal(false);
      setWirelessForm(EMPTY_WIRELESS_FORM);
      loadWirelessEntries();
    } catch (err: any) {
      setWirelessError(err?.response?.data?.detail ?? "Couldn't add this Access List entry.");
    } finally {
      setWirelessSaving(false);
    }
  }

  async function handleRemoveWirelessEntry(entryId: string) {
    if (!confirm("Remove this wireless Access List entry from the router?")) return;
    try {
      await api.post(`/devices/${id}/remove-wireless-entry/`, { entry_id: entryId });
      loadWirelessEntries();
    } catch (err: any) {
      setWirelessError(err?.response?.data?.detail ?? "Couldn't remove that entry.");
    }
  }

  if (!device) return <p className="text-[var(--text-muted)]">Loading…</p>;

  const chartData = readings.map((r) => ({
    time: new Date(r.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    in: r.bandwidth_in_mbps,
    out: r.bandwidth_out_mbps,
    latency: r.latency_ms,
  }));

  return (
    <div>
      <Link to="/admin/networking" className="mb-4 inline-block text-sm text-[var(--series-1)] hover:underline">
        ← Back to networking
      </Link>
      <PageHeader
        title={device.name}
        subtitle={
          `${device.ip_address} · ${device.location} · ${device.vendor} ${device.model_name}` +
          (device.site_name ? ` · Site: ${device.site_name}` : "") +
          (device.visible_partners.length > 0 ? ` · Partners: ${device.visible_partner_names.join(", ")}` : "")
        }
        actions={
          <div className="flex items-center gap-2">
            {device.api_enabled && (
              <>
                <button type="button" className={btnSecondary} disabled={testing} onClick={handleTestConnection}>
                  {testing ? "Testing…" : "Test Connection"}
                </button>
                <button type="button" className={btnSecondary} disabled={polling} onClick={handlePollNow}>
                  {polling ? "Polling…" : "Poll Now"}
                </button>
                {/* Only shown when the shaper is actually on, since with
                    it off this button's only effect is to remove the
                    managed queues -- not what anybody clicking "sync"
                    expects. */}
                {device.enable_shaper && (
                  <button type="button" className={btnSecondary} disabled={syncingShaper} onClick={handleSyncShaper}>
                    {syncingShaper ? "Syncing…" : "Sync shaper queues"}
                  </button>
                )}
              </>
            )}
            <StatusBadge status={device.status} />
            <button
              type="button"
              className="text-sm font-medium text-red-600 hover:underline dark:text-red-400"
              onClick={handleDeleteDevice}
            >
              Delete router
            </button>
          </div>
        }
      />

      {device.api_enabled && (testResult || testError || pollError) && (
        <div
          className={`mb-4 rounded-lg border p-3 text-sm ${
            testError || pollError
              ? "border-red-300 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200"
              : "border-[var(--border-hairline)] bg-[var(--tint-subtle)] text-[var(--text-secondary)]"
          }`}
        >
          {shaperResult && <p>{shaperResult}</p>}
          {testError && <p>Test Connection failed: {testError}</p>}
          {pollError && <p>Poll Now failed: {pollError}</p>}
          {testResult && (
            <p>
              Connected to <strong>{testResult.identity ?? device.name}</strong> — RouterOS {testResult.routeros_version ?? "?"}
              {testResult.board_name ? ` (${testResult.board_name})` : ""}, up {testResult.uptime ?? "?"}, CPU{" "}
              {testResult.cpu_load_pct ?? "?"}%.
            </p>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold">Bandwidth (Mbps)</h2>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={palette.gridline} vertical={false} />
              <XAxis dataKey="time" tick={{ fontSize: 10, fill: palette.textMuted }} axisLine={{ stroke: palette.gridline }} tickLine={false} minTickGap={30} />
              <YAxis tick={{ fontSize: 11, fill: palette.textMuted }} axisLine={false} tickLine={false} width={36} />
              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line type="monotone" dataKey="in" name="Download" stroke={palette.series1} strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="out" name="Upload" stroke={palette.series2} strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>

        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold">Latency (ms)</h2>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={palette.gridline} vertical={false} />
              <XAxis dataKey="time" tick={{ fontSize: 10, fill: palette.textMuted }} axisLine={{ stroke: palette.gridline }} tickLine={false} minTickGap={30} />
              <YAxis tick={{ fontSize: 11, fill: palette.textMuted }} axisLine={false} tickLine={false} width={36} />
              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
              <Line type="monotone" dataKey="latency" name="Latency" stroke={palette.series3} strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {device.api_enabled ? (
        <div className="mt-4 rounded-lg border border-[var(--border-hairline)] bg-[var(--tint-subtle)] p-4 text-xs text-[var(--text-muted)]">
          Monitoring data on this page is real, polled from this device's Mikrotik RouterOS API (see the{" "}
          <code>poll_mikrotik_devices</code> management command, or the Poll Now button above for an on-demand read).
        </div>
      ) : (
        <div className="mt-4 rounded-lg border border-[var(--border-hairline)] bg-[var(--tint-subtle)] p-4 text-xs text-[var(--text-muted)]">
          Monitoring data on this page is simulated for demo purposes (see <code>simulate_monitoring</code> management
          command). Enable the Mikrotik RouterOS API on this router (Edit — under Networking) for real data instead.
        </div>
      )}

      {device.api_enabled && (
        <div className="mt-4 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Live PPP/OVPN sessions (from router)</h2>
            <button type="button" className={btnPrimary} disabled={sessionsLoading} onClick={loadSessions}>
              {sessionsLoading ? "Loading…" : sessions ? "Refresh" : "Load live sessions"}
            </button>
          </div>
          <p className="mb-3 text-xs text-[var(--text-muted)]">
            Read directly from this router's own state via its API — independent of the FreeRADIUS-accounting-based
            Live Sessions view under Networking, which relies on the router having sent accounting packets.
          </p>
          {sessionsError && <p className="mb-3 text-sm text-red-700 dark:text-red-300">{sessionsError}</p>}
          {sessions && (
            <Table>
              <THead>
                <tr>
                  <TH>Name</TH>
                  <TH>Address</TH>
                  <TH>Caller ID</TH>
                  <TH>Uptime</TH>
                  <TH></TH>
                </tr>
              </THead>
              <tbody>
                {sessions.map((s) => (
                  <TR key={s[".id"]}>
                    <TD className="font-medium">{s.name ?? "—"}</TD>
                    <TD>{s.address ?? "—"}</TD>
                    <TD>{s["caller-id"] ?? "—"}</TD>
                    <TD>{s.uptime ?? "—"}</TD>
                    <TD>
                      <button
                        className="text-red-600 hover:underline dark:text-red-400"
                        onClick={() => handleDisconnect(s[".id"])}
                      >
                        Disconnect
                      </button>
                    </TD>
                  </TR>
                ))}
                {sessions.length === 0 && (
                  <TR>
                    <TD className="text-[var(--text-muted)]">No active sessions on this router right now.</TD>
                  </TR>
                )}
              </tbody>
            </Table>
          )}
        </div>
      )}

      {/* Who is behind this box. The point of recording an access device
          on each service is that the question gets asked from THIS end --
          "the sector is down, who do we phone" -- and a record readable
          only one customer at a time answers nothing on that day. */}
      <div className="mt-4 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">
            Customers connecting through this device
            {connected && connected.length > 0 && (
              <span className="ml-2 font-normal text-[var(--text-muted)]">{connected.length}</span>
            )}
          </h2>
          <button type="button" className={btnSecondary} disabled={connectedLoading} onClick={loadConnected}>
            {connectedLoading ? "Loading…" : "Refresh"}
          </button>
        </div>
        {connected === null ? (
          <p className="text-sm text-[var(--text-muted)]">Loading…</p>
        ) : connected.length === 0 ? (
          <p className="text-sm text-[var(--text-muted)]">
            Nobody is recorded as connecting through this device. Set it on a customer&rsquo;s service under
            Edit service &rarr; Access.
          </p>
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Customer</TH>
                <TH>Phone</TH>
                <TH>Port / sector</TH>
                <TH>Plan</TH>
                <TH>Service</TH>
              </tr>
            </THead>
            <tbody>
              {connected.map((c) => (
                <TR key={c.service_id}>
                  <TD>
                    <Link
                      to={`/admin/customers/${c.customer_id}`}
                      className="font-medium text-[var(--series-1)] hover:underline"
                    >
                      {c.customer_name}
                    </Link>
                  </TD>
                  {/* The number, on the row. During an outage the next
                      action is always a phone call, and making somebody
                      open each customer to find it is the difference
                      between a useful list and a report. */}
                  <TD className="tabular-nums">{c.customer_phone || "—"}</TD>
                  <TD>{c.access_detail || <span className="text-[var(--text-muted)]">—</span>}</TD>
                  <TD>{c.tariff_name || "—"}</TD>
                  <TD><StatusBadge status={c.service_status} /></TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </div>

      <div className="mt-4 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Connection rules</h2>
          <button
            type="button"
            className={btnPrimary}
            onClick={() => {
              setRuleForm(EMPTY_RULE_FORM);
              setRuleError("");
              setShowRuleModal(true);
            }}
          >
            + Add rule
          </button>
        </div>
        <p className="mb-3 text-xs text-[var(--text-muted)]">
          Named speed-shaping profiles for this router (Speed Down/Up, plus a guaranteed floor). Assign one to a
          customer's Service (Router & shaping section) to override their tariff's plan speed when this router's
          Shaper is enabled — use "Sync shaper queues" at the top of this page to push them.
        </p>
        <Table>
          <THead>
            <tr>
              <TH>Title</TH>
              <TH>Speed Down</TH>
              <TH>Speed Up</TH>
              <TH>Guaranteed speed limit at</TH>
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {rules.map((r) => (
              <TR key={r.id}>
                <TD className="font-medium">{r.title}</TD>
                <TD className="tabular-nums">{r.speed_down_kbps} kbps</TD>
                <TD className="tabular-nums">{r.speed_up_kbps} kbps</TD>
                <TD className="tabular-nums">{r.guaranteed_pct}%</TD>
                <TD>
                  <button className="text-red-600 hover:underline dark:text-red-400" onClick={() => handleDeleteRule(r)}>
                    Delete
                  </button>
                </TD>
              </TR>
            ))}
            {rules.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]">No connection rules on this router yet.</TD>
              </TR>
            )}
          </tbody>
        </Table>
      </div>

      {device.enable_wireless_access_list && (
        <div className="mt-4 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Wireless Access List{device.enable_mpsk ? " / MPSK" : ""}</h2>
            <div className="flex gap-2">
              <button type="button" className={btnSecondary} disabled={wirelessLoading} onClick={loadWirelessEntries}>
                {wirelessLoading ? "Loading…" : wirelessEntries ? "Refresh" : "Load entries"}
              </button>
              <button
                type="button"
                className={btnPrimary}
                disabled={!device.wireless_interface}
                onClick={() => {
                  setWirelessForm(EMPTY_WIRELESS_FORM);
                  setWirelessError(null);
                  setShowWirelessModal(true);
                }}
              >
                + Add entry
              </button>
            </div>
          </div>
          <p className="mb-3 text-xs text-[var(--text-muted)]">
            Read live from this router's own <code>{device.wireless_interface || "(no interface set)"}</code>{" "}
            wireless Access List via its API.
            {device.enable_mpsk && " Add a passphrase to give one MAC its own WPA2 passphrase (MPSK) instead of the network-wide one."}
          </p>
          {!device.wireless_interface && (
            <p className="mb-3 text-xs text-amber-700 dark:text-amber-400">
              Set this router's wireless interface name (Edit router) before adding entries.
            </p>
          )}
          {wirelessError && <p className="mb-3 text-sm text-red-700 dark:text-red-300">{wirelessError}</p>}
          {wirelessEntries && (
            <Table>
              <THead>
                <tr>
                  <TH>MAC address</TH>
                  <TH>Interface</TH>
                  <TH>Comment</TH>
                  <TH>MPSK</TH>
                  <TH></TH>
                </tr>
              </THead>
              <tbody>
                {wirelessEntries.map((entry) => (
                  <TR key={entry[".id"]}>
                    <TD className="font-medium">{entry["mac-address"] ?? "—"}</TD>
                    <TD>{entry.interface ?? "—"}</TD>
                    <TD>{entry.comment ?? "—"}</TD>
                    <TD>{entry["private-passphrase"] ? "Yes" : "—"}</TD>
                    <TD>
                      <button
                        className="text-red-600 hover:underline dark:text-red-400"
                        onClick={() => handleRemoveWirelessEntry(entry[".id"])}
                      >
                        Remove
                      </button>
                    </TD>
                  </TR>
                ))}
                {wirelessEntries.length === 0 && (
                  <TR>
                    <TD className="text-[var(--text-muted)]">No Access List entries on this router right now.</TD>
                  </TR>
                )}
              </tbody>
            </Table>
          )}
        </div>
      )}

      {showWirelessModal && (
        <Modal title="Add wireless Access List entry" onClose={() => setShowWirelessModal(false)}>
          <form onSubmit={handleAddWirelessEntry}>
            <FormField label="MAC address">
              <input
                className={inputClass}
                required
                placeholder="AA:BB:CC:DD:EE:FF"
                value={wirelessForm.mac_address}
                onChange={(e) => setWirelessForm({ ...wirelessForm, mac_address: e.target.value })}
              />
            </FormField>
            <FormField label="Comment (optional)">
              <input
                className={inputClass}
                value={wirelessForm.comment}
                onChange={(e) => setWirelessForm({ ...wirelessForm, comment: e.target.value })}
              />
            </FormField>
            {device.enable_mpsk && (
              <FormField label="MPSK passphrase (optional)">
                <input
                  className={inputClass}
                  placeholder="Leave blank for a plain allow entry"
                  value={wirelessForm.passphrase}
                  onChange={(e) => setWirelessForm({ ...wirelessForm, passphrase: e.target.value })}
                />
              </FormField>
            )}
            {wirelessError && <p className="mb-3 text-sm text-red-700 dark:text-red-300">{wirelessError}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowWirelessModal(false)}>Cancel</button>
              <button type="submit" disabled={wirelessSaving} className={btnPrimary}>{wirelessSaving ? "Saving…" : "Add"}</button>
            </div>
          </form>
        </Modal>
      )}

      {showRuleModal && (
        <Modal title="Create rule" onClose={() => setShowRuleModal(false)}>
          <form onSubmit={handleAddRule}>
            <FormField label="Title">
              <input
                className={inputClass}
                required
                value={ruleForm.title}
                onChange={(e) => setRuleForm({ ...ruleForm, title: e.target.value })}
              />
            </FormField>
            <FormField label="Speed Down (kbps)">
              <input
                type="number"
                min={0}
                className={inputClass}
                value={ruleForm.speed_down_kbps}
                onChange={(e) => setRuleForm({ ...ruleForm, speed_down_kbps: Number(e.target.value) })}
              />
            </FormField>
            <FormField label="Speed Up (kbps)">
              <input
                type="number"
                min={0}
                className={inputClass}
                value={ruleForm.speed_up_kbps}
                onChange={(e) => setRuleForm({ ...ruleForm, speed_up_kbps: Number(e.target.value) })}
              />
            </FormField>
            <FormField label="Guaranteed speed limit at (%)">
              <input
                type="number"
                min={0}
                max={100}
                className={inputClass}
                value={ruleForm.guaranteed_pct}
                onChange={(e) => setRuleForm({ ...ruleForm, guaranteed_pct: Number(e.target.value) })}
              />
            </FormField>

            {ruleError && <p className="mb-3 text-sm text-[var(--status-critical)]">{ruleError}</p>}

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowRuleModal(false)}>Cancel</button>
              <button type="submit" disabled={ruleSaving} className={btnPrimary}>{ruleSaving ? "Saving…" : "Add"}</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
