import { useEffect, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, SortableTH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { ColumnToggle, type ColumnDef } from "../../components/ColumnToggle";
import { useColumnVisibility } from "../../hooks/useColumnVisibility";
import type { Service, Customer, Tariff } from "../../types";

const COLUMNS: ColumnDef[] = [
  { key: "customer", label: "Customer" },
  { key: "tariff", label: "Tariff" },
  { key: "price", label: "Price" },
  { key: "status", label: "Status" },
  { key: "start_date", label: "Start date" },
];

export function ServicesPage() {
  const [ordering, setOrdering] = useState("-created_at");
  const [statusFilter, setStatusFilter] = useState("");
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("services", ["customer"]);
  const { items, loading, refetch } = useApiList<Service>(
    `/services/?page_size=200&ordering=${ordering}${statusFilter ? `&status=${statusFilter}` : ""}`
  );
  const [customers, setCustomers] = useState<Customer[]>([]);

  function toggleSort(field: string) {
    setOrdering((prev) => (prev === field ? `-${field}` : prev === `-${field}` ? "-created_at" : field));
  }
  const [tariffs, setTariffs] = useState<Tariff[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState<{ customer: string; tariff: string; status: Service["status"] }>({
    customer: "",
    tariff: "",
    status: "pending",
  });

  useEffect(() => {
    api.get<{ results: Customer[] }>("/customers/?page_size=200").then((res) => setCustomers(res.data.results));
    api.get<{ results: Tariff[] }>("/tariffs/?page_size=100").then((res) => setTariffs(res.data.results));
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await api.post("/services/", {
        ...form,
        start_date: new Date().toISOString().slice(0, 10),
      });
      setShowModal(false);
      refetch();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Customer Services"
        subtitle="Active subscriptions linking customers to tariffs."
        actions={
          <button className={btnPrimary} onClick={() => setShowModal(true)}>
            + New service
          </button>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select className={`${inputClass} w-auto`} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          <option value="pending">Pending Activation</option>
          <option value="active">Active</option>
          <option value="suspended">Suspended</option>
          <option value="terminated">Terminated</option>
        </select>
        {statusFilter && (
          <button type="button" className={btnSecondary} onClick={() => setStatusFilter("")}>
            Clear filters
          </button>
        )}
        <div className="ml-auto">
          <ColumnToggle columns={COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["customer"]} />
        </div>
      </div>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <SortableTH field="customer__full_name" ordering={ordering} onSort={toggleSort}>Customer</SortableTH>
              {isVisible("tariff") && <SortableTH field="tariff__name" ordering={ordering} onSort={toggleSort}>Tariff</SortableTH>}
              {isVisible("price") && <SortableTH field="tariff__price" ordering={ordering} onSort={toggleSort}>Price</SortableTH>}
              {isVisible("status") && <SortableTH field="status" ordering={ordering} onSort={toggleSort}>Status</SortableTH>}
              {isVisible("start_date") && <SortableTH field="start_date" ordering={ordering} onSort={toggleSort}>Start date</SortableTH>}
            </tr>
          </THead>
          <tbody>
            {items.map((s) => (
              <TR key={s.id}>
                <TD>{s.customer_name}</TD>
                {isVisible("tariff") && <TD>{s.tariff_name}</TD>}
                {isVisible("price") && <TD className="tabular-nums">R {parseFloat(s.price).toFixed(2)}</TD>}
                {isVisible("status") && <TD><StatusBadge status={s.status} /></TD>}
                {isVisible("start_date") && <TD>{s.start_date ?? "—"}</TD>}
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title="New service" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Customer">
              <select className={inputClass} required value={form.customer} onChange={(e) => setForm({ ...form, customer: e.target.value })}>
                <option value="">Select customer…</option>
                {customers.map((c) => (
                  <option key={c.id} value={c.id}>{c.full_name} ({c.customer_id})</option>
                ))}
              </select>
            </FormField>
            <FormField label="Tariff">
              <select className={inputClass} required value={form.tariff} onChange={(e) => setForm({ ...form, tariff: e.target.value })}>
                <option value="">Select tariff…</option>
                {tariffs.map((t) => (
                  <option key={t.id} value={t.id}>{t.name} — R{t.price}</option>
                ))}
              </select>
            </FormField>
            <FormField label="Status">
              <select className={inputClass} value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value as Service["status"] })}>
                <option value="pending">Pending Activation</option>
                <option value="active">Active</option>
                <option value="suspended">Suspended</option>
                <option value="terminated">Terminated</option>
              </select>
            </FormField>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>Cancel</button>
              <button type="submit" disabled={saving} className={btnPrimary}>{saving ? "Saving…" : "Create service"}</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
