import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, filterSelectClass, btnPrimary, btnSecondary } from "../../components/Modal";
import type { User, Vehicle } from "../../types";

const EMPTY_FORM = {
  make: "",
  model: "",
  year: String(new Date().getFullYear()),
  registration_number: "",
  fuel_type: "petrol",
  assigned_to: "",
  service_interval_km: "10000",
  notes: "",
};

export function VehiclesPage() {
  const navigate = useNavigate();
  const [assignedFilter, setAssignedFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [staffList, setStaffList] = useState<User[]>([]);
  const { items, loading, refetch } = useApiList<Vehicle>(
    `/vehicles/?page_size=200${assignedFilter ? `&assigned_to=${assignedFilter}` : ""}`
  );
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get<{ results: User[] }>("/staff-users/?page_size=200").then((r) => setStaffList(r.data.results));
  }, []);

  const filteredItems = statusFilter ? items.filter((v) => v.service_status === statusFilter) : items;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      await api.post("/vehicles/", {
        make: form.make,
        model: form.model,
        year: Number(form.year),
        registration_number: form.registration_number,
        fuel_type: form.fuel_type,
        assigned_to: form.assigned_to || null,
        service_interval_km: Number(form.service_interval_km),
        notes: form.notes,
      });
      setShowModal(false);
      setForm(EMPTY_FORM);
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: unknown } })?.response?.data;
      setError(
        typeof detail === "string"
          ? detail
          : (detail as { registration_number?: string[]; non_field_errors?: string[] })?.registration_number?.[0] ||
              (detail as { non_field_errors?: string[] })?.non_field_errors?.[0] ||
              "Failed to save vehicle."
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Vehicles"
        subtitle="Fleet vehicles, assigned drivers, odometer history, and service intervals."
        actions={
          <button className={btnPrimary} onClick={() => setShowModal(true)}>
            + New vehicle
          </button>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select className={filterSelectClass} value={assignedFilter} onChange={(e) => setAssignedFilter(e.target.value)}>
          <option value="">All drivers</option>
          {staffList.map((s) => (
            <option key={s.id} value={s.id}>
              {s.first_name || s.username} {s.last_name}
            </option>
          ))}
        </select>
        <select className={filterSelectClass} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All service statuses</option>
          <option value="ok">OK</option>
          <option value="due_soon">Due soon</option>
          <option value="overdue">Overdue</option>
          <option value="in_service">In service</option>
        </select>
        {(assignedFilter || statusFilter) && (
          <button
            type="button"
            className={btnSecondary}
            onClick={() => {
              setAssignedFilter("");
              setStatusFilter("");
            }}
          >
            Clear filters
          </button>
        )}
      </div>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Registration</TH>
              <TH>Make / model</TH>
              <TH>Year</TH>
              <TH>Fuel</TH>
              <TH>Assigned to</TH>
              <TH>Current km</TH>
              <TH>Next service due</TH>
              <TH>Status</TH>
            </tr>
          </THead>
          <tbody>
            {filteredItems.map((v) => (
              <TR key={v.id} onClick={() => navigate(`/admin/vehicles/${v.id}`)}>
                <TD className="font-medium">{v.registration_number}</TD>
                <TD>
                  {v.make} {v.model}
                </TD>
                <TD>{v.year}</TD>
                <TD className="capitalize">{v.fuel_type}</TD>
                <TD>{v.assigned_to_name || "—"}</TD>
                <TD className="tabular-nums">{v.current_odometer_km.toLocaleString()} km</TD>
                <TD className="tabular-nums">{v.next_service_due_km.toLocaleString()} km</TD>
                <TD>
                  <StatusBadge status={v.service_status} />
                  {/* A flag would say it's at the workshop; the count says
                      whether that's still reasonable. Eleven days off the
                      road is a different conversation from booked in this
                      morning. */}
                  {v.service_status === "in_service" && v.days_in_service != null && (
                    <span className="ml-2 text-xs text-[var(--text-muted)]">
                      {v.days_in_service === 0
                        ? "since today"
                        : `${v.days_in_service} day${v.days_in_service === 1 ? "" : "s"}`}
                    </span>
                  )}
                </TD>
              </TR>
            ))}
            {filteredItems.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]">No vehicles match the current filters.</TD>
              </TR>
            )}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title="New vehicle" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Make">
              <input
                className={inputClass}
                required
                value={form.make}
                onChange={(e) => setForm({ ...form, make: e.target.value })}
              />
            </FormField>
            <FormField label="Model">
              <input
                className={inputClass}
                required
                value={form.model}
                onChange={(e) => setForm({ ...form, model: e.target.value })}
              />
            </FormField>
            <FormField label="Year">
              <input
                type="number"
                className={inputClass}
                required
                min="1950"
                max="2100"
                value={form.year}
                onChange={(e) => setForm({ ...form, year: e.target.value })}
              />
            </FormField>
            <FormField label="Registration number">
              <input
                className={inputClass}
                required
                value={form.registration_number}
                onChange={(e) => setForm({ ...form, registration_number: e.target.value })}
              />
            </FormField>
            <FormField label="Fuel type">
              <select
                className={inputClass}
                value={form.fuel_type}
                onChange={(e) => setForm({ ...form, fuel_type: e.target.value })}
              >
                <option value="petrol">Petrol</option>
                <option value="diesel">Diesel</option>
              </select>
            </FormField>
            <FormField label="Assigned to (optional)">
              <select
                className={inputClass}
                value={form.assigned_to}
                onChange={(e) => setForm({ ...form, assigned_to: e.target.value })}
              >
                <option value="">Unassigned</option>
                {staffList.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.first_name || s.username} {s.last_name}
                  </option>
                ))}
              </select>
            </FormField>
            <FormField label="Service interval (km)">
              <input
                type="number"
                className={inputClass}
                required
                min="1"
                value={form.service_interval_km}
                onChange={(e) => setForm({ ...form, service_interval_km: e.target.value })}
              />
            </FormField>
            <FormField label="Notes (optional)">
              <input
                className={inputClass}
                value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
              />
            </FormField>
            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" className={btnPrimary} disabled={saving}>
                {saving ? "Saving…" : "Create vehicle"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
