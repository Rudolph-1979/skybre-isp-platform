import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useParams, Link } from "react-router-dom";
import { api } from "../../api/client";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import type { FuelLog, OdometerReading, ServiceRecord, StaffProfile, User, Vehicle } from "../../types";

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString();
}

function nowForInput() {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

function todayForInput() {
  return new Date().toISOString().slice(0, 10);
}

export function VehicleDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [vehicle, setVehicle] = useState<Vehicle | null>(null);
  const [readings, setReadings] = useState<OdometerReading[]>([]);
  const [services, setServices] = useState<ServiceRecord[]>([]);
  const [fuelLogs, setFuelLogs] = useState<FuelLog[]>([]);
  const [staffList, setStaffList] = useState<User[]>([]);
  const [driverProfile, setDriverProfile] = useState<StaffProfile | null>(null);

  const [showEdit, setShowEdit] = useState(false);
  const [editForm, setEditForm] = useState({
    make: "", model: "", year: "", registration_number: "", fuel_type: "petrol", assigned_to: "",
    service_interval_km: "", is_active: true, notes: "",
  });
  const [savingEdit, setSavingEdit] = useState(false);
  const [editError, setEditError] = useState("");

  const [showReading, setShowReading] = useState(false);
  const [readingForm, setReadingForm] = useState({ reading_km: "", recorded_at: nowForInput(), notes: "" });
  const [savingReading, setSavingReading] = useState(false);
  const [readingError, setReadingError] = useState("");

  const [showService, setShowService] = useState(false);
  const [serviceForm, setServiceForm] = useState({ service_date: todayForInput(), odometer_km: "", notes: "" });
  const [savingService, setSavingService] = useState(false);
  const [serviceError, setServiceError] = useState("");

  const [showFuelLog, setShowFuelLog] = useState(false);
  const [fuelLogForm, setFuelLogForm] = useState({ filled_at: todayForInput(), litres: "", odometer_km: "", notes: "" });
  const [savingFuelLog, setSavingFuelLog] = useState(false);
  const [fuelLogError, setFuelLogError] = useState("");
  const [bookingIn, setBookingIn] = useState(false);

  async function toggleInService() {
    if (!id || !vehicle) return;
    setBookingIn(true);
    try {
      // Booking OUT is deliberately separate from logging a service:
      // "the booking was cancelled" and "the service happened" are
      // different events, and only the second one should reset the km
      // countdown. Logging a service clears this by itself.
      await api.post(`/vehicles/${id}/book-in/`, {
        in_service_since: vehicle.in_service_since ? null : new Date().toISOString().slice(0, 10),
      });
      refetchAll();
    } finally {
      setBookingIn(false);
    }
  }

  function refetchAll() {
    if (!id) return;
    api.get<Vehicle>(`/vehicles/${id}/`).then((res) => setVehicle(res.data));
    api
      .get<{ results: OdometerReading[] }>(`/odometer-readings/?vehicle=${id}&page_size=100`)
      .then((res) => setReadings(res.data.results));
    api
      .get<{ results: ServiceRecord[] }>(`/service-records/?vehicle=${id}&page_size=100`)
      .then((res) => setServices(res.data.results));
    api
      .get<{ results: FuelLog[] }>(`/fuel-logs/?vehicle=${id}&page_size=100`)
      .then((res) => setFuelLogs(res.data.results));
  }

  useEffect(() => {
    refetchAll();
    api.get<{ results: User[] }>("/staff-users/?page_size=200").then((r) => setStaffList(r.data.results));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => {
    if (!vehicle?.assigned_to) {
      setDriverProfile(null);
      return;
    }
    // Driver ID/license details live on the staff payroll profile, which is
    // admin-only -- a non-admin viewing this vehicle just won't see them
    // (fails silently rather than showing an error for an optional extra).
    api
      .get<{ results: StaffProfile[] }>(`/staff-profiles/?user=${vehicle.assigned_to}`)
      .then((r) => setDriverProfile(r.data.results[0] || null))
      .catch(() => setDriverProfile(null));
  }, [vehicle?.assigned_to]);

  function openEdit() {
    if (!vehicle) return;
    setEditForm({
      make: vehicle.make,
      model: vehicle.model,
      year: String(vehicle.year),
      registration_number: vehicle.registration_number,
      fuel_type: vehicle.fuel_type,
      assigned_to: vehicle.assigned_to ? String(vehicle.assigned_to) : "",
      service_interval_km: String(vehicle.service_interval_km),
      is_active: vehicle.is_active,
      notes: vehicle.notes,
    });
    setEditError("");
    setShowEdit(true);
  }

  async function handleEditSubmit(e: FormEvent) {
    e.preventDefault();
    setEditError("");
    setSavingEdit(true);
    try {
      await api.patch(`/vehicles/${id}/`, {
        make: editForm.make,
        model: editForm.model,
        year: Number(editForm.year),
        registration_number: editForm.registration_number,
        fuel_type: editForm.fuel_type,
        assigned_to: editForm.assigned_to || null,
        service_interval_km: Number(editForm.service_interval_km),
        is_active: editForm.is_active,
        notes: editForm.notes,
      });
      setShowEdit(false);
      refetchAll();
    } catch (err) {
      const detail = (err as { response?: { data?: unknown } })?.response?.data;
      setEditError(
        typeof detail === "string"
          ? detail
          : (detail as { registration_number?: string[] })?.registration_number?.[0] || "Failed to save vehicle."
      );
    } finally {
      setSavingEdit(false);
    }
  }

  async function handleDeleteVehicle() {
    if (!vehicle) return;
    if (!confirm(`Delete ${vehicle.registration_number} (${vehicle.make} ${vehicle.model})? This also deletes its odometer, service, and fuel history.`))
      return;
    await api.delete(`/vehicles/${id}/`);
    navigate("/admin/vehicles");
  }

  async function handleReadingSubmit(e: FormEvent) {
    e.preventDefault();
    setReadingError("");
    setSavingReading(true);
    try {
      await api.post("/odometer-readings/", {
        vehicle: Number(id),
        reading_km: Number(readingForm.reading_km),
        recorded_at: new Date(readingForm.recorded_at).toISOString(),
        notes: readingForm.notes,
      });
      setShowReading(false);
      setReadingForm({ reading_km: "", recorded_at: nowForInput(), notes: "" });
      refetchAll();
    } catch (err) {
      const detail = (err as { response?: { data?: unknown } })?.response?.data;
      setReadingError(
        typeof detail === "string"
          ? detail
          : (detail as { non_field_errors?: string[] })?.non_field_errors?.[0] || "Failed to save reading."
      );
    } finally {
      setSavingReading(false);
    }
  }

  async function handleServiceSubmit(e: FormEvent) {
    e.preventDefault();
    setServiceError("");
    setSavingService(true);
    try {
      await api.post(`/vehicles/${id}/mark_serviced/`, {
        service_date: serviceForm.service_date,
        odometer_km: Number(serviceForm.odometer_km),
        notes: serviceForm.notes,
      });
      setShowService(false);
      setServiceForm({ service_date: todayForInput(), odometer_km: "", notes: "" });
      refetchAll();
    } catch (err) {
      const detail = (err as { response?: { data?: unknown } })?.response?.data as { detail?: string } | undefined;
      setServiceError(detail?.detail || "Failed to log service.");
    } finally {
      setSavingService(false);
    }
  }

  async function handleFuelLogSubmit(e: FormEvent) {
    e.preventDefault();
    setFuelLogError("");
    setSavingFuelLog(true);
    try {
      await api.post("/fuel-logs/", {
        vehicle: Number(id),
        filled_at: fuelLogForm.filled_at,
        litres: fuelLogForm.litres,
        odometer_km: Number(fuelLogForm.odometer_km),
        notes: fuelLogForm.notes,
      });
      setShowFuelLog(false);
      setFuelLogForm({ filled_at: todayForInput(), litres: "", odometer_km: "", notes: "" });
      refetchAll();
    } catch (err) {
      const detail = (err as { response?: { data?: unknown } })?.response?.data;
      setFuelLogError(
        typeof detail === "string"
          ? detail
          : (detail as { non_field_errors?: string[] })?.non_field_errors?.[0] || "Failed to log fill-up."
      );
    } finally {
      setSavingFuelLog(false);
    }
  }

  if (!vehicle) return <p className="text-[var(--text-muted)]">Loading…</p>;

  return (
    <div>
      <Link to="/admin/vehicles" className="mb-4 inline-block text-sm text-[var(--series-1)] hover:underline">
        ← Back to vehicles
      </Link>
      <PageHeader
        title={`${vehicle.year} ${vehicle.make} ${vehicle.model}`}
        subtitle={`${vehicle.registration_number} · ${vehicle.fuel_type === "diesel" ? "Diesel" : "Petrol"} · ${vehicle.assigned_to_name || "Unassigned"}${vehicle.is_active ? "" : " · Inactive"}`}
        actions={
          <>
            <StatusBadge status={vehicle.service_status} />
            <button className={btnSecondary} disabled={bookingIn} onClick={toggleInService}>
              {vehicle.in_service_since ? "Back from workshop" : "Book in for service"}
            </button>
            <button className={btnSecondary} onClick={openEdit}>
              Edit
            </button>
            <button
              className="rounded-md border border-[var(--status-critical)] px-4 py-2 text-sm font-medium text-[var(--status-critical)] hover:bg-[var(--tint-hover)]"
              onClick={handleDeleteVehicle}
            >
              Delete
            </button>
          </>
        }
      />

      {vehicle.in_service_since && (
        <div className="mb-6 rounded-lg border border-[var(--series-1)]/40 bg-[var(--series-1)]/8 px-4 py-3 text-sm text-[var(--text-secondary)]">
          At the workshop since <strong className="text-[var(--text-primary)]">{vehicle.in_service_since}</strong>
          {vehicle.days_in_service != null && vehicle.days_in_service > 0 && (
            <> — {vehicle.days_in_service} day{vehicle.days_in_service === 1 ? "" : "s"}</>
          )}
          . Logging the service below brings it back out automatically.
        </div>
      )}

      {vehicle.assigned_to && (
        <div className="mb-6 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">Driver</p>
          {driverProfile ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 text-sm">
              <div>
                <p className="text-[var(--text-muted)]">Name</p>
                <p className="font-medium text-[var(--text-primary)]">{vehicle.assigned_to_name}</p>
              </div>
              <div>
                <p className="text-[var(--text-muted)]">ID number</p>
                <p className="font-medium text-[var(--text-primary)]">{driverProfile.id_number || "—"}</p>
              </div>
              <div>
                <p className="text-[var(--text-muted)]">License number</p>
                <p className="font-medium text-[var(--text-primary)]">{driverProfile.license_number || "—"}</p>
              </div>
              <div>
                <p className="text-[var(--text-muted)]">Contact number</p>
                <p className="font-medium text-[var(--text-primary)]">{driverProfile.phone || "—"}</p>
              </div>
            </div>
          ) : (
            <p className="text-sm text-[var(--text-primary)]">
              {vehicle.assigned_to_name} —{" "}
              <span className="text-[var(--text-muted)]">
                no ID/license on file (add one under Staff → Employees).
              </span>
            </p>
          )}
        </div>
      )}

      <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">Current km</p>
          <p className="mt-1 text-lg font-semibold tabular-nums text-[var(--text-primary)]">
            {vehicle.current_odometer_km.toLocaleString()}
          </p>
        </div>
        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">Service interval</p>
          <p className="mt-1 text-lg font-semibold tabular-nums text-[var(--text-primary)]">
            {vehicle.service_interval_km.toLocaleString()} km
          </p>
        </div>
        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">Last serviced</p>
          <p className="mt-1 text-lg font-semibold text-[var(--text-primary)]">
            {vehicle.last_service_date ? formatDate(vehicle.last_service_date) : "Never"}
          </p>
          {vehicle.last_service_date && (
            <p className="text-xs text-[var(--text-muted)]">at {vehicle.last_service_km.toLocaleString()} km</p>
          )}
        </div>
        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">Next service due</p>
          <p className="mt-1 text-lg font-semibold tabular-nums text-[var(--text-primary)]">
            {vehicle.next_service_due_km.toLocaleString()} km
          </p>
        </div>
        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">
            {vehicle.km_until_due >= 0 ? "Km until due" : "Km overdue by"}
          </p>
          <p
            className={`mt-1 text-lg font-semibold tabular-nums ${
              vehicle.service_status === "overdue" ? "text-[var(--status-critical)]" : "text-[var(--text-primary)]"
            }`}
          >
            {Math.abs(vehicle.km_until_due).toLocaleString()}
          </p>
        </div>
        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">Fuel type</p>
          <p className="mt-1 text-lg font-semibold capitalize text-[var(--text-primary)]">{vehicle.fuel_type}</p>
        </div>
        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">Total litres used</p>
          <p className="mt-1 text-lg font-semibold tabular-nums text-[var(--text-primary)]">
            {Number(vehicle.total_litres_used).toLocaleString()} L
          </p>
        </div>
        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">Avg. consumption</p>
          <p className="mt-1 text-lg font-semibold tabular-nums text-[var(--text-primary)]">
            {vehicle.average_km_per_litre != null ? `${vehicle.average_km_per_litre} km/L` : "—"}
          </p>
        </div>
      </div>

      {vehicle.notes && (
        <div className="mb-6 rounded-lg border border-[var(--border-hairline)] bg-[var(--tint-subtle)] p-4 text-sm text-[var(--text-secondary)]">
          {vehicle.notes}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">Odometer history</h2>
            <button className={btnSecondary} onClick={() => setShowReading(true)}>
              + Log reading
            </button>
          </div>
          <Table>
            <THead>
              <tr>
                <TH>Date</TH>
                <TH>Reading</TH>
                <TH>Logged by</TH>
                <TH>Notes</TH>
              </tr>
            </THead>
            <tbody>
              {readings.map((r) => (
                <TR key={r.id}>
                  <TD>{formatDate(r.recorded_at)}</TD>
                  <TD className="tabular-nums">{r.reading_km.toLocaleString()} km</TD>
                  <TD>{r.recorded_by_name || "—"}</TD>
                  <TD>{r.notes || "—"}</TD>
                </TR>
              ))}
              {readings.length === 0 && (
                <TR>
                  <TD className="text-[var(--text-muted)]">No odometer readings logged yet.</TD>
                </TR>
              )}
            </tbody>
          </Table>
        </div>

        <div>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">Service history</h2>
            <button className={btnPrimary} onClick={() => setShowService(true)}>
              Mark as serviced
            </button>
          </div>
          <Table>
            <THead>
              <tr>
                <TH>Date</TH>
                <TH>Odometer</TH>
                <TH>Logged by</TH>
                <TH>Notes</TH>
              </tr>
            </THead>
            <tbody>
              {services.map((s) => (
                <TR key={s.id}>
                  <TD>{formatDate(s.service_date)}</TD>
                  <TD className="tabular-nums">{s.odometer_km.toLocaleString()} km</TD>
                  <TD>{s.logged_by_name || "—"}</TD>
                  <TD>{s.notes || "—"}</TD>
                </TR>
              ))}
              {services.length === 0 && (
                <TR>
                  <TD className="text-[var(--text-muted)]">No service records logged yet.</TD>
                </TR>
              )}
            </tbody>
          </Table>
        </div>

        <div>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">Fuel log</h2>
            <button className={btnSecondary} onClick={() => setShowFuelLog(true)}>
              + Log fill-up
            </button>
          </div>
          <Table>
            <THead>
              <tr>
                <TH>Date</TH>
                <TH>Litres</TH>
                <TH>Odometer</TH>
                <TH>Logged by</TH>
              </tr>
            </THead>
            <tbody>
              {fuelLogs.map((f) => (
                <TR key={f.id}>
                  <TD>{formatDate(f.filled_at)}</TD>
                  <TD className="tabular-nums">{Number(f.litres).toLocaleString()} L</TD>
                  <TD className="tabular-nums">{f.odometer_km.toLocaleString()} km</TD>
                  <TD>{f.logged_by_name || "—"}</TD>
                </TR>
              ))}
              {fuelLogs.length === 0 && (
                <TR>
                  <TD className="text-[var(--text-muted)]">No fuel logged yet.</TD>
                </TR>
              )}
            </tbody>
          </Table>
        </div>
      </div>

      {showEdit && (
        <Modal title="Edit vehicle" onClose={() => setShowEdit(false)}>
          <form onSubmit={handleEditSubmit}>
            <FormField label="Make">
              <input
                className={inputClass}
                required
                value={editForm.make}
                onChange={(e) => setEditForm({ ...editForm, make: e.target.value })}
              />
            </FormField>
            <FormField label="Model">
              <input
                className={inputClass}
                required
                value={editForm.model}
                onChange={(e) => setEditForm({ ...editForm, model: e.target.value })}
              />
            </FormField>
            <FormField label="Year">
              <input
                type="number"
                className={inputClass}
                required
                min="1950"
                max="2100"
                value={editForm.year}
                onChange={(e) => setEditForm({ ...editForm, year: e.target.value })}
              />
            </FormField>
            <FormField label="Registration number">
              <input
                className={inputClass}
                required
                value={editForm.registration_number}
                onChange={(e) => setEditForm({ ...editForm, registration_number: e.target.value })}
              />
            </FormField>
            <FormField label="Fuel type">
              <select
                className={inputClass}
                value={editForm.fuel_type}
                onChange={(e) => setEditForm({ ...editForm, fuel_type: e.target.value })}
              >
                <option value="petrol">Petrol</option>
                <option value="diesel">Diesel</option>
              </select>
            </FormField>
            <FormField label="Assigned to">
              <select
                className={inputClass}
                value={editForm.assigned_to}
                onChange={(e) => setEditForm({ ...editForm, assigned_to: e.target.value })}
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
                value={editForm.service_interval_km}
                onChange={(e) => setEditForm({ ...editForm, service_interval_km: e.target.value })}
              />
            </FormField>
            <FormField label="Notes">
              <input
                className={inputClass}
                value={editForm.notes}
                onChange={(e) => setEditForm({ ...editForm, notes: e.target.value })}
              />
            </FormField>
            <label className="mb-3 flex items-center gap-2 text-sm text-[var(--text-primary)]">
              <input
                type="checkbox"
                checked={editForm.is_active}
                onChange={(e) => setEditForm({ ...editForm, is_active: e.target.checked })}
              />
              Active
            </label>
            {editError && <p className="mb-3 text-sm text-[var(--status-critical)]">{editError}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowEdit(false)}>
                Cancel
              </button>
              <button type="submit" className={btnPrimary} disabled={savingEdit}>
                {savingEdit ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {showReading && (
        <Modal title="Log odometer reading" onClose={() => setShowReading(false)}>
          <form onSubmit={handleReadingSubmit}>
            <FormField label="Reading (km)">
              <input
                type="number"
                className={inputClass}
                required
                min="0"
                value={readingForm.reading_km}
                onChange={(e) => setReadingForm({ ...readingForm, reading_km: e.target.value })}
              />
            </FormField>
            <FormField label="Date & time">
              <input
                type="datetime-local"
                className={inputClass}
                required
                value={readingForm.recorded_at}
                onChange={(e) => setReadingForm({ ...readingForm, recorded_at: e.target.value })}
              />
            </FormField>
            <FormField label="Notes (optional)">
              <input
                className={inputClass}
                value={readingForm.notes}
                onChange={(e) => setReadingForm({ ...readingForm, notes: e.target.value })}
              />
            </FormField>
            {readingError && <p className="mb-3 text-sm text-[var(--status-critical)]">{readingError}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowReading(false)}>
                Cancel
              </button>
              <button type="submit" className={btnPrimary} disabled={savingReading}>
                {savingReading ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {showService && (
        <Modal title="Mark as serviced" onClose={() => setShowService(false)}>
          <form onSubmit={handleServiceSubmit}>
            <FormField label="Service date">
              <input
                type="date"
                className={inputClass}
                required
                value={serviceForm.service_date}
                onChange={(e) => setServiceForm({ ...serviceForm, service_date: e.target.value })}
              />
            </FormField>
            <FormField label="Odometer at service (km)">
              <input
                type="number"
                className={inputClass}
                required
                min="0"
                placeholder={String(vehicle.current_odometer_km)}
                value={serviceForm.odometer_km}
                onChange={(e) => setServiceForm({ ...serviceForm, odometer_km: e.target.value })}
              />
            </FormField>
            <FormField label="Notes (optional)">
              <input
                className={inputClass}
                placeholder="e.g. Oil + filter, brake pads"
                value={serviceForm.notes}
                onChange={(e) => setServiceForm({ ...serviceForm, notes: e.target.value })}
              />
            </FormField>
            {serviceError && <p className="mb-3 text-sm text-[var(--status-critical)]">{serviceError}</p>}
            <p className="mb-3 text-xs text-[var(--text-muted)]">
              This resets the next service due to {vehicle.service_interval_km.toLocaleString()} km after the
              odometer value entered above.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowService(false)}>
                Cancel
              </button>
              <button type="submit" className={btnPrimary} disabled={savingService}>
                {savingService ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {showFuelLog && (
        <Modal title="Log fuel fill-up" onClose={() => setShowFuelLog(false)}>
          <form onSubmit={handleFuelLogSubmit}>
            <FormField label="Date">
              <input
                type="date"
                className={inputClass}
                required
                value={fuelLogForm.filled_at}
                onChange={(e) => setFuelLogForm({ ...fuelLogForm, filled_at: e.target.value })}
              />
            </FormField>
            <FormField label="Litres">
              <input
                type="number"
                step="0.01"
                className={inputClass}
                required
                min="0"
                value={fuelLogForm.litres}
                onChange={(e) => setFuelLogForm({ ...fuelLogForm, litres: e.target.value })}
              />
            </FormField>
            <FormField label="Odometer at fill-up (km)">
              <input
                type="number"
                className={inputClass}
                required
                min="0"
                placeholder={String(vehicle.current_odometer_km)}
                value={fuelLogForm.odometer_km}
                onChange={(e) => setFuelLogForm({ ...fuelLogForm, odometer_km: e.target.value })}
              />
            </FormField>
            <FormField label="Notes (optional)">
              <input
                className={inputClass}
                value={fuelLogForm.notes}
                onChange={(e) => setFuelLogForm({ ...fuelLogForm, notes: e.target.value })}
              />
            </FormField>
            {fuelLogError && <p className="mb-3 text-sm text-[var(--status-critical)]">{fuelLogError}</p>}
            <p className="mb-3 text-xs text-[var(--text-muted)]">
              Average consumption is calculated from the distance covered between fill-ups, so it becomes
              accurate once at least two fill-ups are logged.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowFuelLog(false)}>
                Cancel
              </button>
              <button type="submit" className={btnPrimary} disabled={savingFuelLog}>
                {savingFuelLog ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
