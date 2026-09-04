import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { SearchableSelect } from "../../components/SearchableSelect";
import { Table, THead, TH, SortableTH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, filterSelectClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { ColumnToggle, type ColumnDef } from "../../components/ColumnToggle";
import { useColumnVisibility } from "../../hooks/useColumnVisibility";
import {
  ServiceConnectionFields, serviceConnectionPayload, emptyServiceConnectionValues,
  type ServiceConnectionValues,
} from "../../components/ServiceConnectionFields";
import {
  ServiceShapingFields, serviceShapingPayload, emptyServiceShapingValues,
  type ServiceShapingValues,
} from "../../components/ServiceShapingFields";
import { LiveBandwidthWidget } from "../../components/LiveBandwidthWidget";
import type { Service, Customer, Tariff, IPPool, IPAddress, Device, ConnectionRule } from "../../types";

const COLUMNS: ColumnDef[] = [
  { key: "customer", label: "Customer" },
  { key: "tariff", label: "Tariff" },
  { key: "price", label: "Price" },
  { key: "status", label: "Status" },
  { key: "start_date", label: "Start date" },
  { key: "radius", label: "RADIUS login" },
];

type NewServiceForm = { customer: string; tariff: string; status: Service["status"] } & ServiceConnectionValues &
  ServiceShapingValues;

const emptyNewServiceForm: NewServiceForm = {
  customer: "", tariff: "", status: "pending", ...emptyServiceConnectionValues, ...emptyServiceShapingValues,
};

type EditForm = { status: Service["status"] } & ServiceConnectionValues & ServiceShapingValues;

const emptyEditForm: EditForm = { status: "pending", ...emptyServiceConnectionValues, ...emptyServiceShapingValues };

export function ServicesPage() {
  const [ordering, setOrdering] = useState("-created_at");
  // Seeded from the URL so a dashboard tile can land you on a filtered view
  // rather than the unfiltered page, leaving you to re-apply the filter you
  // just clicked. Lazy initialiser: read once on mount, then it's ordinary
  // state the user is free to change.
  const [searchParams] = useSearchParams();
  const [statusFilter, setStatusFilter] = useState(() => searchParams.get("status") ?? "");
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
  const [newError, setNewError] = useState<string | null>(null);
  const [form, setForm] = useState<NewServiceForm>(emptyNewServiceForm);
  const [editingService, setEditingService] = useState<Service | null>(null);
  const [editForm, setEditForm] = useState<EditForm>(emptyEditForm);
  const [editSaving, setEditSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [customerPools, setCustomerPools] = useState<IPPool[]>([]);
  const [newPoolAddresses, setNewPoolAddresses] = useState<IPAddress[]>([]);
  const [poolAddresses, setPoolAddresses] = useState<IPAddress[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [connectionRules, setConnectionRules] = useState<ConnectionRule[]>([]);

  useEffect(() => {
    api.get<{ results: Customer[] }>("/customers/picker/").then((res) => setCustomers(res.data.results));
    api.get<{ results: Tariff[] }>("/tariffs/?page_size=100").then((res) => setTariffs(res.data.results));
    // Used by the PPPoE IP-assignment section of the create/edit modals
    // below -- fetched once up front the same way customers/tariffs are.
    api.get<{ results: IPPool[] }>("/ip-pools/?category=customer&page_size=200").then((res) => setCustomerPools(res.data.results));
    // Used by the Router & shaping section of the create/edit modals.
    api.get<{ results: Device[] }>("/devices/?page_size=200").then((res) => setDevices(res.data.results));
    api.get<{ results: ConnectionRule[] }>("/connection-rules/?page_size=500").then((res) => setConnectionRules(res.data.results));
  }, []);

  // Free addresses in the pool chosen on the "New service" form.
  useEffect(() => {
    if (form.ip_assignment_mode === "pool" && form.ip_pool) {
      api.get<{ results: IPAddress[] }>(`/ip-addresses/?pool=${form.ip_pool}&page_size=500`).then((res) =>
        setNewPoolAddresses(res.data.results)
      );
    } else {
      setNewPoolAddresses([]);
    }
  }, [form.ip_assignment_mode, form.ip_pool]);

  // Free (or already-held-by-this-service) addresses in the pool chosen on
  // the "Edit service" form -- refetched whenever that selection changes.
  useEffect(() => {
    if (editForm.ip_assignment_mode === "pool" && editForm.ip_pool) {
      api.get<{ results: IPAddress[] }>(`/ip-addresses/?pool=${editForm.ip_pool}&page_size=500`).then((res) =>
        setPoolAddresses(res.data.results)
      );
    } else {
      setPoolAddresses([]);
    }
  }, [editForm.ip_assignment_mode, editForm.ip_pool]);

  function openCreate() {
    setForm(emptyNewServiceForm);
    setNewError(null);
    setShowModal(true);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setNewError(null);
    try {
      await api.post("/services/", {
        customer: form.customer,
        tariff: form.tariff,
        status: form.status,
        start_date: new Date().toISOString().slice(0, 10),
        ...serviceConnectionPayload(form),
        ...serviceShapingPayload(form),
      });
      setShowModal(false);
      refetch();
    } catch (err: any) {
      const data = err?.response?.data;
      const message = data && typeof data === "object" ? Object.values(data).flat().join(" ") : null;
      setNewError(message || "Couldn't create this service.");
    } finally {
      setSaving(false);
    }
  }

  function openEdit(service: Service) {
    setEditingService(service);
    setEditError(null);
    setEditForm({
      status: service.status,
      radius_username: service.radius_username ?? "",
      radius_password: "",
      radius_connection_type: service.radius_connection_type,
      ip_assignment_mode: service.ip_assignment_mode,
      static_ip: service.static_ip ?? "",
      ip_pool: service.ip_pool ? String(service.ip_pool) : "",
      ip_address: "",
      device: service.device ? String(service.device) : "",
      connection_rule: service.connection_rule ? String(service.connection_rule) : "",
      access_device: service.access_device ? String(service.access_device) : "",
      access_detail: service.access_detail ?? "",
      fup_threshold_gb: service.fup_threshold_gb == null ? "" : String(service.fup_threshold_gb),
      fup_speed_pct: service.fup_speed_pct == null ? "" : String(service.fup_speed_pct),
      fup_exempt: service.fup_exempt ?? false,
    });
  }

  async function handleEditSubmit(e: FormEvent) {
    e.preventDefault();
    if (!editingService) return;
    setEditSaving(true);
    setEditError(null);
    try {
      await api.patch(`/services/${editingService.id}/`, {
        status: editForm.status,
        ...serviceConnectionPayload(editForm),
        ...serviceShapingPayload(editForm),
      });
      setEditingService(null);
      refetch();
    } catch (err: any) {
      const data = err?.response?.data;
      const message = data && typeof data === "object"
        ? Object.values(data).flat().join(" ")
        : "Couldn't save this service.";
      setEditError(message || "Couldn't save this service.");
    } finally {
      setEditSaving(false);
    }
  }

  async function handleDelete(service: Service) {
    if (
      !confirm(
        `Delete the ${service.tariff_name} service for ${service.customer_name}? This also releases its RADIUS ` +
          "login and any assigned IP address. This can't be undone."
      )
    )
      return;
    try {
      await api.delete(`/services/${service.id}/`);
      if (editingService?.id === service.id) setEditingService(null);
      refetch();
    } catch (err: any) {
      alert(err?.response?.data?.detail || "Couldn't delete this service.");
    }
  }

  return (
    <div>
      <PageHeader
        title="Customer Services"
        subtitle="Active subscriptions linking customers to tariffs."
        actions={
          <button className={btnPrimary} onClick={openCreate}>
            + New service
          </button>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select className={filterSelectClass} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
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
              {isVisible("radius") && <SortableTH field="radius_username" ordering={ordering} onSort={toggleSort}>RADIUS login</SortableTH>}
              <TH></TH>
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
                {isVisible("radius") && <TD>{s.radius_username ?? "—"}</TD>}
                <TD>
                  <div className="flex gap-3">
                    <button className="text-[var(--series-1)] hover:underline" onClick={() => openEdit(s)}>
                      Edit
                    </button>
                    <button
                      className="text-red-600 hover:underline dark:text-red-400"
                      onClick={() => handleDelete(s)}
                    >
                      Delete
                    </button>
                  </div>
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title="New service" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Customer">
<SearchableSelect
                options={customers.map((c) => ({
                  value: String(c.id),
                  label: c.full_name,
                  meta: c.customer_id,
                  searchText: `${c.full_name} ${c.company_name ?? ""} ${c.customer_id}`,
                }))}
                value={form.customer}
                onChange={(v) => setForm({ ...form, customer: v })}
                placeholder="Select customer…"
                hint="Search by name or payment reference."
                required
              />
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

            <ServiceConnectionFields
              values={form}
              onChange={(patch) => setForm({ ...form, ...patch })}
              customerPools={customerPools}
              poolAddresses={newPoolAddresses}
            />

            <ServiceShapingFields
              values={form}
              onChange={(patch) => setForm({ ...form, ...patch })}
              devices={devices}
              connectionRules={connectionRules}
            />

            {newError && <p className="mt-3 text-sm text-red-700 dark:text-red-300">{newError}</p>}

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>Cancel</button>
              <button type="submit" disabled={saving} className={btnPrimary}>{saving ? "Saving…" : "Create service"}</button>
            </div>
          </form>
        </Modal>
      )}

      {editingService && (
        <Modal title={`Edit service — ${editingService.customer_name}`} onClose={() => setEditingService(null)}>
          <form onSubmit={handleEditSubmit}>
            <FormField label="Status">
              <select
                className={inputClass}
                value={editForm.status}
                onChange={(e) => setEditForm({ ...editForm, status: e.target.value as Service["status"] })}
              >
                <option value="pending">Pending Activation</option>
                <option value="active">Active</option>
                <option value="suspended">Suspended</option>
                <option value="terminated">Terminated</option>
              </select>
            </FormField>

            <ServiceConnectionFields
              values={editForm}
              onChange={(patch) => setEditForm({ ...editForm, ...patch })}
              customerPools={customerPools}
              poolAddresses={poolAddresses}
              passwordIsSet={editingService.radius_password_set}
              currentServiceId={editingService.id}
              currentAssignedIp={editingService.assigned_ip}
            />

            <ServiceShapingFields
              values={editForm}
              onChange={(patch) => setEditForm({ ...editForm, ...patch })}
              devices={devices}
              connectionRules={connectionRules}
            />

            {editForm.device && <LiveBandwidthWidget serviceId={editingService.id} />}

            {editError && (
              <p className="mt-3 text-sm text-red-700 dark:text-red-300">{editError}</p>
            )}

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setEditingService(null)}>Cancel</button>
              <button type="submit" disabled={editSaving} className={btnPrimary}>{editSaving ? "Saving…" : "Save changes"}</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
