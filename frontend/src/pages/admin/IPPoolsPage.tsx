import { useEffect, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { ColumnToggle, type ColumnDef } from "../../components/ColumnToggle";
import { useColumnVisibility } from "../../hooks/useColumnVisibility";
import type { IPPool, IPAddress } from "../../types";

const POOL_COLUMNS: ColumnDef[] = [
  { key: "name", label: "Name" },
  { key: "cidr", label: "CIDR" },
  { key: "gateway", label: "Gateway" },
  { key: "type", label: "Type" },
  { key: "free_total", label: "Free / Total" },
];

const ADDRESS_COLUMNS: ColumnDef[] = [
  { key: "address", label: "Address" },
  { key: "status", label: "Status" },
  { key: "assigned_service", label: "Assigned service" },
];

export function IPPoolsPage() {
  const { items, loading, refetch } = useApiList<IPPool>("/ip-pools/?page_size=100");
  const [showModal, setShowModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState<Partial<IPPool>>({ name: "", network_cidr: "", gateway: "", pool_type: "ipv4" });
  const [selectedPool, setSelectedPool] = useState<number | null>(null);
  const [addresses, setAddresses] = useState<IPAddress[]>([]);
  const [addressStatusFilter, setAddressStatusFilter] = useState("");
  const { hidden: hiddenPoolCols, isVisible: isPoolColVisible, toggle: togglePoolCol } = useColumnVisibility("ip-pools", ["name"]);
  const { hidden: hiddenAddrCols, isVisible: isAddrColVisible, toggle: toggleAddrCol } = useColumnVisibility("ip-addresses", ["address"]);

  useEffect(() => {
    if (selectedPool == null) return;
    const statusParam = addressStatusFilter ? `&status=${addressStatusFilter}` : "";
    api
      .get<{ results: IPAddress[] }>(`/ip-addresses/?pool=${selectedPool}&page_size=200${statusParam}`)
      .then((res) => setAddresses(res.data.results));
  }, [selectedPool, addressStatusFilter]);

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

      <div className="mb-4 flex justify-end">
        <ColumnToggle columns={POOL_COLUMNS} hidden={hiddenPoolCols} onToggle={togglePoolCol} alwaysVisible={["name"]} />
      </div>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Name</TH>
              {isPoolColVisible("cidr") && <TH>CIDR</TH>}
              {isPoolColVisible("gateway") && <TH>Gateway</TH>}
              {isPoolColVisible("type") && <TH>Type</TH>}
              {isPoolColVisible("free_total") && <TH>Free / Total</TH>}
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((p) => (
              <TR key={p.id}>
                <TD className="font-medium">{p.name}</TD>
                {isPoolColVisible("cidr") && <TD>{p.network_cidr}</TD>}
                {isPoolColVisible("gateway") && <TD>{p.gateway ?? "—"}</TD>}
                {isPoolColVisible("type") && <TD className="uppercase">{p.pool_type}</TD>}
                {isPoolColVisible("free_total") && <TD className="tabular-nums">{p.free_count} / {p.total_count}</TD>}
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
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold">Addresses in pool</h2>
            <div className="flex items-center gap-2">
              <select
                className={`${inputClass} w-auto`}
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
