import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, filterSelectClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { ColumnToggle, type ColumnDef } from "../../components/ColumnToggle";
import { useColumnVisibility } from "../../hooks/useColumnVisibility";
import type { Device } from "../../types";

const EMPTY: Partial<Device> = { name: "", device_type: "router", ip_address: "", location: "", vendor: "", model_name: "" };

const COLUMNS: ColumnDef[] = [
  { key: "name", label: "Name" },
  { key: "type", label: "Type" },
  { key: "ip_address", label: "IP Address" },
  { key: "location", label: "Location" },
  { key: "status", label: "Status" },
  { key: "latency", label: "Latency" },
  { key: "bandwidth", label: "Bandwidth (in/out)" },
];

export function DevicesPage() {
  const navigate = useNavigate();
  const [typeFilter, setTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("devices", ["name"]);
  const { items, loading, refetch } = useApiList<Device>(
    `/devices/?page_size=100${typeFilter ? `&device_type=${typeFilter}` : ""}${statusFilter ? `&status=${statusFilter}` : ""}`
  );
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState<Partial<Device>>(EMPTY);
  const [saving, setSaving] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await api.post("/devices/", form);
      setShowModal(false);
      setForm(EMPTY);
      refetch();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Network Devices"
        subtitle="Routers, switches, OLTs and access points. Monitoring readings are simulated for this demo — see the README for wiring in real SNMP polling."
        actions={
          <button className={btnPrimary} onClick={() => setShowModal(true)}>+ New device</button>
        }
      />

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
          <ColumnToggle columns={COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["name"]} />
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
              {isVisible("status") && <TH>Status</TH>}
              {isVisible("latency") && <TH>Latency</TH>}
              {isVisible("bandwidth") && <TH>Bandwidth (in/out)</TH>}
            </tr>
          </THead>
          <tbody>
            {items.map((d) => (
              <TR key={d.id} onClick={() => navigate(`/admin/devices/${d.id}`)}>
                <TD className="font-medium">{d.name}</TD>
                {isVisible("type") && <TD className="capitalize">{d.device_type.replace("_", " ")}</TD>}
                {isVisible("ip_address") && <TD>{d.ip_address}</TD>}
                {isVisible("location") && <TD>{d.location}</TD>}
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
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title="New device" onClose={() => setShowModal(false)}>
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
            <FormField label="Vendor">
              <input className={inputClass} value={form.vendor} onChange={(e) => setForm({ ...form, vendor: e.target.value })} />
            </FormField>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>Cancel</button>
              <button type="submit" disabled={saving} className={btnPrimary}>{saving ? "Saving…" : "Create device"}</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
