import { useEffect, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import type { IPPool, IPAddress } from "../../types";

export function IPPoolsPage() {
  const { items, loading, refetch } = useApiList<IPPool>("/ip-pools/?page_size=100");
  const [showModal, setShowModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState<Partial<IPPool>>({ name: "", network_cidr: "", gateway: "", pool_type: "ipv4" });
  const [selectedPool, setSelectedPool] = useState<number | null>(null);
  const [addresses, setAddresses] = useState<IPAddress[]>([]);

  useEffect(() => {
    if (selectedPool == null) return;
    api.get<{ results: IPAddress[] }>(`/ip-addresses/?pool=${selectedPool}&page_size=200`).then((res) => setAddresses(res.data.results));
  }, [selectedPool]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await api.post("/ip-pools/", { ...form, gateway: form.gateway || null });
      setShowModal(false);
      refetch();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="IP Address Pools"
        subtitle="IPv4/IPv6 ranges and per-customer address assignment."
        actions={<button className={btnPrimary} onClick={() => setShowModal(true)}>+ New pool</button>}
      />

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Name</TH>
              <TH>CIDR</TH>
              <TH>Gateway</TH>
              <TH>Type</TH>
              <TH>Free / Total</TH>
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((p) => (
              <TR key={p.id}>
                <TD className="font-medium">{p.name}</TD>
                <TD>{p.network_cidr}</TD>
                <TD>{p.gateway ?? "—"}</TD>
                <TD className="uppercase">{p.pool_type}</TD>
                <TD className="tabular-nums">{p.free_count} / {p.total_count}</TD>
                <TD>
                  <button className="text-[var(--series-1)] hover:underline" onClick={() => setSelectedPool(p.id)}>
                    View addresses
                  </button>
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {selectedPool != null && (
        <div className="mt-6">
          <h2 className="mb-2 text-sm font-semibold">Addresses in pool</h2>
          <Table>
            <THead>
              <tr>
                <TH>Address</TH>
                <TH>Status</TH>
                <TH>Assigned service</TH>
              </tr>
            </THead>
            <tbody>
              {addresses.map((a) => (
                <TR key={a.id}>
                  <TD>{a.address}</TD>
                  <TD><StatusBadge status={a.status} /></TD>
                  <TD>{a.assigned_service ?? "—"}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </div>
      )}

      {showModal && (
        <Modal title="New IP pool" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Name">
              <input className={inputClass} required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </FormField>
            <FormField label="Network CIDR">
              <input className={inputClass} required placeholder="10.20.0.0/24" value={form.network_cidr} onChange={(e) => setForm({ ...form, network_cidr: e.target.value })} />
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
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>Cancel</button>
              <button type="submit" disabled={saving} className={btnPrimary}>{saving ? "Saving…" : "Create pool"}</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
