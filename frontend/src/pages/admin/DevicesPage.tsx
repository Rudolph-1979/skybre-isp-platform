import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import type { Device } from "../../types";

const EMPTY: Partial<Device> = { name: "", device_type: "router", ip_address: "", location: "", vendor: "", model_name: "" };

export function DevicesPage() {
  const navigate = useNavigate();
  const { items, loading, refetch } = useApiList<Device>("/devices/?page_size=100");
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

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Name</TH>
              <TH>Type</TH>
              <TH>IP Address</TH>
              <TH>Location</TH>
              <TH>Status</TH>
              <TH>Latency</TH>
              <TH>Bandwidth (in/out)</TH>
            </tr>
          </THead>
          <tbody>
            {items.map((d) => (
              <TR key={d.id} onClick={() => navigate(`/admin/devices/${d.id}`)}>
                <TD className="font-medium">{d.name}</TD>
                <TD className="capitalize">{d.device_type.replace("_", " ")}</TD>
                <TD>{d.ip_address}</TD>
                <TD>{d.location}</TD>
                <TD><StatusBadge status={d.status} /></TD>
                <TD className="tabular-nums">{d.latest_reading?.latency_ms != null ? `${d.latest_reading.latency_ms} ms` : "—"}</TD>
                <TD className="tabular-nums">
                  {d.latest_reading?.bandwidth_in_mbps != null
                    ? `${d.latest_reading.bandwidth_in_mbps} / ${d.latest_reading.bandwidth_out_mbps} Mbps`
                    : "—"}
                </TD>
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
