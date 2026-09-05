import { useEffect, useMemo, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { SearchableSelect } from "../../components/SearchableSelect";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { DayScheduleGrid } from "../../components/DayScheduleGrid";
import { apiErrorMessage } from "../../utils/apiError";
import type { Job, Shift, Customer, User } from "../../types";

const JOB_TYPE_COLOR: Record<Job["job_type"], string> = {
  installation: "#2a78d6",
  repair: "#e34948",
  maintenance: "#eda100",
  site_visit: "#1baf7a",
  office_task: "#4a3aa7",
  other: "#898781",
};
const SHIFT_COLOR = "#eb6834";

function toLocalDateKey(iso: string) {
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function toDateTimeLocalInput(iso: string) {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const EMPTY_JOB = {
  customer: "",
  ticket: "",
  assigned_to: "",
  job_type: "site_visit" as Job["job_type"],
  title: "",
  description: "",
  status: "scheduled" as Job["status"],
  start: "",
  end: "",
  location: "",
};

const EMPTY_SHIFT = {
  staff: "",
  start: "",
  end: "",
  role_note: "",
  status: "planned" as Shift["status"],
  notes: "",
};

type JobFormState = typeof EMPTY_JOB;
type ShiftFormState = typeof EMPTY_SHIFT;

export function SchedulingPage() {
  const [monthAnchor, setMonthAnchor] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });
  const [staffFilter, setStaffFilter] = useState("");
  const [staff, setStaff] = useState<User[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);

  // Month grid or a single day against the hours. Clicking a date in the
  // month view switches to Day for that date, and Prev/Next then step one
  // day at a time -- so the same two buttons mean "month" or "day"
  // depending on what you're looking at.
  const [view, setView] = useState<"month" | "day">("month");
  const [dayAnchor, setDayAnchor] = useState(() => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d;
  });
  const [jobModal, setJobModal] = useState<{ job: JobFormState; editingId: number | null } | null>(null);
  const [shiftModal, setShiftModal] = useState<{ shift: ShiftFormState; editingId: number | null } | null>(null);
  const [saving, setSaving] = useState(false);
  // What the API said about the last save or delete, one message per modal.
  // Until now every one of them was thrown away -- see handleJobSubmit.
  const [jobError, setJobError] = useState("");
  const [shiftError, setShiftError] = useState("");

  useEffect(() => {
    api.get<{ results: User[] }>("/staff-users/?page_size=100").then((res) => setStaff(res.data.results));
    // /customers/picker/ rather than the paginated list, because page_size is
    // capped at 500 and DRF clamps it silently: this dropdown held the first
    // 500 customers by name with nothing saying so, so a job for anyone whose
    // surname sorted past roughly the 500th could not be booked against them
    // at all -- about 1,092 of our 1,592 customers. The picker is unpaginated,
    // already ordered by full_name (hence no &ordering here), and partner-
    // scoped the same way the list is. Finance, Services and Tickets moved to
    // it already; Scheduling was the one left behind.
    api.get<{ results: Customer[] }>("/customers/picker/").then((res) => setCustomers(res.data.results));
  }, []);

  // Grid always spans 6 full weeks (Sunday-start) covering the whole month.
  const gridStart = useMemo(() => {
    const d = new Date(monthAnchor);
    d.setDate(d.getDate() - d.getDay());
    return d;
  }, [monthAnchor]);
  const gridDays = useMemo(() => Array.from({ length: 42 }, (_, i) => {
    const d = new Date(gridStart);
    d.setDate(d.getDate() + i);
    return d;
  }), [gridStart]);
  const gridEnd = gridDays[gridDays.length - 1];

  // Only fetch what the current view shows. Day view asking for six weeks of
  // jobs would be wasteful, and month view asking for one day would be
  // wrong.
  //
  // Both filter on an item's START, so something that began yesterday and
  // runs into today does not appear today. That matches how the month grid
  // has always behaved; worth knowing rather than assuming otherwise.
  const rangeParams = useMemo(() => {
    if (view === "day") {
      const from = new Date(dayAnchor);
      from.setHours(0, 0, 0, 0);
      const to = new Date(dayAnchor);
      to.setHours(23, 59, 59, 999);
      return `start_from=${from.toISOString()}&start_to=${to.toISOString()}`;
    }
    return `start_from=${gridStart.toISOString()}&start_to=${new Date(
      gridEnd.getFullYear(), gridEnd.getMonth(), gridEnd.getDate(), 23, 59, 59
    ).toISOString()}`;
  }, [view, dayAnchor, gridStart, gridEnd]);
  const staffParam = staffFilter ? `&assigned_to=${staffFilter}` : "";
  const shiftStaffParam = staffFilter ? `&staff=${staffFilter}` : "";

  const { items: jobs, loading: jobsLoading, refetch: refetchJobs } = useApiList<Job>(
    `/jobs/?page_size=500&${rangeParams}${staffParam}`
  );
  const { items: shifts, loading: shiftsLoading, refetch: refetchShifts } = useApiList<Shift>(
    `/shifts/?page_size=500&${rangeParams}${shiftStaffParam}`
  );

  const jobsByDay = useMemo(() => {
    const map = new Map<string, Job[]>();
    jobs.forEach((j) => {
      const key = toLocalDateKey(j.start);
      map.set(key, [...(map.get(key) ?? []), j]);
    });
    return map;
  }, [jobs]);

  const shiftsByDay = useMemo(() => {
    const map = new Map<string, Shift[]>();
    shifts.forEach((s) => {
      const key = toLocalDateKey(s.start);
      map.set(key, [...(map.get(key) ?? []), s]);
    });
    return map;
  }, [shifts]);

  const monthLabel = monthAnchor.toLocaleDateString("en-ZA", { month: "long", year: "numeric" });
  const dayLabel = dayAnchor.toLocaleDateString("en-ZA", {
    weekday: "long", day: "numeric", month: "long", year: "numeric",
  });

  /** Prev/Next move by one month or one day, depending on the view. */
  function step(delta: number) {
    if (view === "day") {
      setDayAnchor((d) => {
        const next = new Date(d);
        next.setDate(next.getDate() + delta);
        return next;
      });
    } else {
      setMonthAnchor((d) => new Date(d.getFullYear(), d.getMonth() + delta, 1));
    }
  }

  function goToToday() {
    const now = new Date();
    setMonthAnchor(new Date(now.getFullYear(), now.getMonth(), 1));
    const d = new Date(now);
    d.setHours(0, 0, 0, 0);
    setDayAnchor(d);
  }

  function openDayView(day: Date) {
    const d = new Date(day);
    d.setHours(0, 0, 0, 0);
    setDayAnchor(d);
    setView("day");
  }

  function openNewJob(prefillDate?: Date) {
    const base = prefillDate ?? new Date();
    const start = new Date(base);
    // A date with no time (from the month grid) defaults to 09:00; a click
    // on a specific hour in the day view keeps that hour.
    if (start.getHours() === 0 && start.getMinutes() === 0) start.setHours(9, 0, 0, 0);
    start.setSeconds(0, 0);
    const end = new Date(start);
    end.setHours(start.getHours() + 1);
    setJobModal({
      job: { ...EMPTY_JOB, start: toDateTimeLocalInput(start.toISOString()), end: toDateTimeLocalInput(end.toISOString()) },
      editingId: null,
    });
  }

  function openEditJob(j: Job) {
    setJobModal({
      job: {
        customer: j.customer ? String(j.customer) : "",
        ticket: j.ticket ? String(j.ticket) : "",
        assigned_to: j.assigned_to ? String(j.assigned_to) : "",
        job_type: j.job_type,
        title: j.title,
        description: j.description,
        status: j.status,
        start: toDateTimeLocalInput(j.start),
        end: toDateTimeLocalInput(j.end),
        location: j.location,
      },
      editingId: j.id,
    });
  }

  function openNewShift(prefillDate?: Date) {
    const base = prefillDate ?? new Date();
    const start = new Date(base);
    // Same rule as openNewJob: midnight means "no time given", so fall back
    // to a normal shift start; an explicit hour is respected.
    if (start.getHours() === 0 && start.getMinutes() === 0) start.setHours(8, 0, 0, 0);
    start.setSeconds(0, 0);
    const end = new Date(start);
    end.setHours(start.getHours() + 8);
    setShiftModal({
      shift: { ...EMPTY_SHIFT, start: toDateTimeLocalInput(start.toISOString()), end: toDateTimeLocalInput(end.toISOString()) },
      editingId: null,
    });
  }

  function openEditShift(s: Shift) {
    setShiftModal({
      shift: {
        staff: String(s.staff),
        start: toDateTimeLocalInput(s.start),
        end: toDateTimeLocalInput(s.end),
        role_note: s.role_note,
        status: s.status,
        notes: s.notes,
      },
      editingId: s.id,
    });
  }

  async function handleJobSubmit(e: FormEvent) {
    e.preventDefault();
    if (!jobModal) return;
    setSaving(true);
    setJobError("");
    try {
      const payload = {
        ...jobModal.job,
        customer: jobModal.job.customer || null,
        ticket: jobModal.job.ticket || null,
        assigned_to: jobModal.job.assigned_to || null,
        start: new Date(jobModal.job.start).toISOString(),
        end: new Date(jobModal.job.end).toISOString(),
      };
      if (jobModal.editingId) {
        await api.patch(`/jobs/${jobModal.editingId}/`, payload);
      } else {
        await api.post("/jobs/", payload);
      }
      setJobModal(null);
      refetchJobs();
    } catch (err) {
      // Easy to hit and previously invisible: neither datetime field stops
      // you putting End before Start, and JobSerializer.validate rejects
      // that with "End time must be after the start time." With no catch
      // the 400 was swallowed, so Save just did nothing -- the modal sat
      // there, the job was never booked, and nobody was sent out. A
      // partner-scoped 403 on someone else's job looked identical.
      setJobError(apiErrorMessage(err, "Could not save this job — please try again."));
    } finally {
      setSaving(false);
    }
  }

  async function handleDeleteJob() {
    if (!jobModal?.editingId) return;
    setSaving(true);
    setJobError("");
    try {
      await api.delete(`/jobs/${jobModal.editingId}/`);
      setJobModal(null);
      refetchJobs();
    } catch (err) {
      // A refused delete used to leave the modal open with nothing said, so
      // it read as a click that hadn't registered -- and the job stayed in
      // the diary.
      setJobError(apiErrorMessage(err, "Could not delete this job — please try again."));
    } finally {
      setSaving(false);
    }
  }

  async function handleShiftSubmit(e: FormEvent) {
    e.preventDefault();
    if (!shiftModal) return;
    setSaving(true);
    setShiftError("");
    try {
      const payload = {
        ...shiftModal.shift,
        staff: shiftModal.shift.staff || null,
        start: new Date(shiftModal.shift.start).toISOString(),
        end: new Date(shiftModal.shift.end).toISOString(),
      };
      if (shiftModal.editingId) {
        await api.patch(`/shifts/${shiftModal.editingId}/`, payload);
      } else {
        await api.post("/shifts/", payload);
      }
      setShiftModal(null);
      refetchShifts();
    } catch (err) {
      // Same silence as the job form, and it matters more here: a shift
      // that failed to save is a rota gap nobody was told about.
      // ShiftSerializer.validate rejects end <= start, which an 18:00-to-
      // 02:00 night shift typed as one day gets wrong every time.
      setShiftError(apiErrorMessage(err, "Could not save this shift — please try again."));
    } finally {
      setSaving(false);
    }
  }

  async function handleDeleteShift() {
    if (!shiftModal?.editingId) return;
    setSaving(true);
    setShiftError("");
    try {
      await api.delete(`/shifts/${shiftModal.editingId}/`);
      setShiftModal(null);
      refetchShifts();
    } catch (err) {
      setShiftError(apiErrorMessage(err, "Could not delete this shift — please try again."));
    } finally {
      setSaving(false);
    }
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      // Don't hijack arrow keys while someone is editing a field or has a
      // form open.
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (jobModal || shiftModal) return;
      step(e.key === "ArrowLeft" ? -1 : 1);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, jobModal, shiftModal]);

  const loading = jobsLoading || shiftsLoading;

  return (
    <div>
      <PageHeader
        title="Scheduling"
        subtitle="Field jobs and staff shifts, office and field."
        actions={
          <>
            <button className={btnSecondary} onClick={() => openNewShift()}>
              + New shift
            </button>
            <button className={btnPrimary} onClick={() => openNewJob()}>
              + New job
            </button>
          </>
        }
      />

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <button className={btnSecondary} onClick={() => step(-1)} title="Previous">
            ‹ Prev
          </button>
          <button className={btnSecondary} onClick={goToToday}>
            Today
          </button>
          <button className={btnSecondary} onClick={() => step(1)} title="Next">
            Next ›
          </button>
          <span className="ml-2 text-sm font-semibold text-[var(--text-primary)]">
            {view === "day" ? dayLabel : monthLabel}
          </span>

          <div className="ml-2 inline-flex overflow-hidden rounded-md border border-[var(--border-hairline)]">
            {(["month", "day"] as const).map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => setView(v)}
                className={`px-3 py-1.5 text-sm font-medium capitalize ${
                  view === v
                    ? "bg-[var(--series-1)] text-white"
                    : "text-[var(--text-secondary)] hover:bg-[var(--tint-hover)]"
                }`}
              >
                {v}
              </button>
            ))}
          </div>
        </div>

        <select className={`${inputClass} max-w-xs`} value={staffFilter} onChange={(e) => setStaffFilter(e.target.value)}>
          <option value="">All staff</option>
          {staff.map((s) => (
            <option key={s.id} value={s.id}>{s.first_name || s.username}</option>
          ))}
        </select>
      </div>

      <div className="mb-3 flex flex-wrap gap-3 text-xs text-[var(--text-muted)]">
        {Object.entries(JOB_TYPE_COLOR).map(([type, color]) => (
          <span key={type} className="inline-flex items-center gap-1.5 capitalize">
            <span className="h-2 w-2 rounded-full" style={{ background: color }} />
            {type.replace("_", " ")}
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full" style={{ background: SHIFT_COLOR }} />
          Shift
        </span>
      </div>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : view === "day" ? (
        <DayScheduleGrid
          day={dayAnchor}
          jobs={jobsByDay.get(toLocalDateKey(dayAnchor.toISOString())) ?? []}
          shifts={shiftsByDay.get(toLocalDateKey(dayAnchor.toISOString())) ?? []}
          jobColour={(j) => JOB_TYPE_COLOR[j.job_type]}
          shiftColour={SHIFT_COLOR}
          onNewJob={openNewJob}
          onNewShift={openNewShift}
          onEditJob={openEditJob}
          onEditShift={openEditShift}
        />
      ) : (
        <div className="overflow-x-auto">
        <div className="grid min-w-[640px] grid-cols-7 overflow-hidden rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] shadow-sm">
          {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((d) => (
            <div key={d} className="border-b border-[var(--border-hairline)] bg-[var(--tint-subtle)] px-2 py-1.5 text-center text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
              {d}
            </div>
          ))}
          {gridDays.map((day, idx) => {
            const key = toLocalDateKey(day.toISOString());
            const dayJobs = jobsByDay.get(key) ?? [];
            const dayShifts = shiftsByDay.get(key) ?? [];
            const inMonth = day.getMonth() === monthAnchor.getMonth();
            const isToday = toLocalDateKey(new Date().toISOString()) === key;
            const totalItems = dayJobs.length + dayShifts.length;
            const visibleJobs = dayJobs.slice(0, 3);
            const visibleShifts = dayShifts.slice(0, Math.max(0, 3 - visibleJobs.length));
            const overflow = totalItems - visibleJobs.length - visibleShifts.length;

            return (
              <div
                key={idx}
                className={`min-h-[110px] border-b border-r border-[var(--border-hairline)] p-1.5 ${
                  inMonth ? "" : "bg-[var(--tint-subtle)]"
                }`}
              >
                <button
                  type="button"
                  onClick={() => openDayView(day)}
                  title="Open this day's hourly schedule"
                  className={`mb-1 rounded px-1.5 py-0.5 text-xs font-medium hover:bg-[var(--tint-hover)] ${
                    isToday
                      ? "bg-[var(--series-1)] text-white"
                      : inMonth
                      ? "text-[var(--text-primary)]"
                      : "text-[var(--text-muted)]"
                  }`}
                >
                  {day.getDate()}
                </button>
                <div className="space-y-1">
                  {visibleJobs.map((j) => (
                    <button
                      key={`job-${j.id}`}
                      type="button"
                      onClick={() => openEditJob(j)}
                      className="block w-full truncate rounded px-1.5 py-0.5 text-left text-[11px] font-medium text-white"
                      style={{ background: JOB_TYPE_COLOR[j.job_type], opacity: j.status === "cancelled" ? 0.45 : 1 }}
                      title={`${j.title} — ${j.customer_name ?? "standalone"}`}
                    >
                      {new Date(j.start).toLocaleTimeString("en-ZA", { hour: "2-digit", minute: "2-digit" })} {j.title}
                    </button>
                  ))}
                  {visibleShifts.map((s) => (
                    <button
                      key={`shift-${s.id}`}
                      type="button"
                      onClick={() => openEditShift(s)}
                      className="block w-full truncate rounded px-1.5 py-0.5 text-left text-[11px] font-medium text-white"
                      style={{ background: SHIFT_COLOR, opacity: s.status === "cancelled" ? 0.45 : 1 }}
                      title={`${s.staff_name} shift`}
                    >
                      {new Date(s.start).toLocaleTimeString("en-ZA", { hour: "2-digit", minute: "2-digit" })} {s.staff_name}
                    </button>
                  ))}
                  {overflow > 0 && (
                    <button
                      type="button"
                      onClick={() => openDayView(day)}
                      className="block w-full px-1.5 text-left text-[11px] text-[var(--text-muted)] hover:underline"
                    >
                      +{overflow} more
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        </div>
      )}

      {jobModal && (
        <Modal
          title={jobModal.editingId ? "Edit job" : "New job"}
          onClose={() => { setJobModal(null); setJobError(""); }}
        >
          <form onSubmit={handleJobSubmit}>
            {jobError && (
              <p className="mb-3 rounded-md border border-[var(--status-critical)] bg-[var(--tint-subtle)] p-2 text-sm text-[var(--status-critical)]">
                {jobError}
              </p>
            )}
            <FormField label="Title">
              <input
                className={inputClass}
                required
                value={jobModal.job.title}
                onChange={(e) => setJobModal({ ...jobModal, job: { ...jobModal.job, title: e.target.value } })}
              />
            </FormField>
            <FormField label="Customer (optional — leave blank for a standalone job)">
              {/* Not `required`: a standalone office task genuinely has no
                  customer, which is why emptyLabel spells that out rather
                  than leaving the picker's default "Clear selection". */}
              <SearchableSelect
                options={customers.map((c) => ({
                  value: String(c.id),
                  label: c.full_name,
                  meta: c.customer_id,
                  searchText: `${c.full_name} ${c.company_name ?? ""} ${c.customer_id}`,
                }))}
                value={jobModal.job.customer}
                onChange={(v) => setJobModal({ ...jobModal, job: { ...jobModal.job, customer: v } })}
                placeholder="No customer (standalone)"
                emptyLabel="No customer (standalone)"
                hint="Search by name or payment reference."
              />
            </FormField>
            <FormField label="Job type">
              <select
                className={inputClass}
                value={jobModal.job.job_type}
                onChange={(e) => setJobModal({ ...jobModal, job: { ...jobModal.job, job_type: e.target.value as Job["job_type"] } })}
              >
                <option value="installation">Installation</option>
                <option value="repair">Repair</option>
                <option value="maintenance">Maintenance</option>
                <option value="site_visit">Site Visit</option>
                <option value="office_task">Office Task</option>
                <option value="other">Other</option>
              </select>
            </FormField>
            <FormField label="Assigned to">
              <select
                className={inputClass}
                value={jobModal.job.assigned_to}
                onChange={(e) => setJobModal({ ...jobModal, job: { ...jobModal.job, assigned_to: e.target.value } })}
              >
                <option value="">Unassigned</option>
                {staff.map((s) => (
                  <option key={s.id} value={s.id}>{s.first_name || s.username}</option>
                ))}
              </select>
            </FormField>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <FormField label="Start">
                <input
                  type="datetime-local"
                  className={inputClass}
                  required
                  value={jobModal.job.start}
                  onChange={(e) => setJobModal({ ...jobModal, job: { ...jobModal.job, start: e.target.value } })}
                />
              </FormField>
              <FormField label="End">
                <input
                  type="datetime-local"
                  className={inputClass}
                  required
                  value={jobModal.job.end}
                  onChange={(e) => setJobModal({ ...jobModal, job: { ...jobModal.job, end: e.target.value } })}
                />
              </FormField>
            </div>
            <FormField label="Location">
              <input
                className={inputClass}
                placeholder="Defaults to customer's address if left blank"
                value={jobModal.job.location}
                onChange={(e) => setJobModal({ ...jobModal, job: { ...jobModal.job, location: e.target.value } })}
              />
            </FormField>
            <FormField label="Status">
              <select
                className={inputClass}
                value={jobModal.job.status}
                onChange={(e) => setJobModal({ ...jobModal, job: { ...jobModal.job, status: e.target.value as Job["status"] } })}
              >
                <option value="scheduled">Scheduled</option>
                <option value="in_progress">In Progress</option>
                <option value="completed">Completed</option>
                <option value="cancelled">Cancelled</option>
              </select>
            </FormField>
            <FormField label="Notes">
              <textarea
                className={inputClass}
                rows={2}
                value={jobModal.job.description}
                onChange={(e) => setJobModal({ ...jobModal, job: { ...jobModal.job, description: e.target.value } })}
              />
            </FormField>
            <div className="mt-4 flex justify-between gap-2">
              {jobModal.editingId ? (
                <button type="button" className="text-sm font-medium text-[var(--status-critical)] hover:underline" onClick={handleDeleteJob}>
                  Delete job
                </button>
              ) : <span />}
              <div className="flex gap-2">
                <button type="button" className={btnSecondary} onClick={() => { setJobModal(null); setJobError(""); }}>Cancel</button>
                <button type="submit" disabled={saving} className={btnPrimary}>{saving ? "Saving…" : "Save"}</button>
              </div>
            </div>
          </form>
        </Modal>
      )}

      {shiftModal && (
        <Modal
          title={shiftModal.editingId ? "Edit shift" : "New shift"}
          onClose={() => { setShiftModal(null); setShiftError(""); }}
        >
          <form onSubmit={handleShiftSubmit}>
            {shiftError && (
              <p className="mb-3 rounded-md border border-[var(--status-critical)] bg-[var(--tint-subtle)] p-2 text-sm text-[var(--status-critical)]">
                {shiftError}
              </p>
            )}
            <FormField label="Staff member">
              <select
                className={inputClass}
                required
                value={shiftModal.shift.staff}
                onChange={(e) => setShiftModal({ ...shiftModal, shift: { ...shiftModal.shift, staff: e.target.value } })}
              >
                <option value="">Select staff…</option>
                {staff.map((s) => (
                  <option key={s.id} value={s.id}>{s.first_name || s.username}</option>
                ))}
              </select>
            </FormField>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <FormField label="Start">
                <input
                  type="datetime-local"
                  className={inputClass}
                  required
                  value={shiftModal.shift.start}
                  onChange={(e) => setShiftModal({ ...shiftModal, shift: { ...shiftModal.shift, start: e.target.value } })}
                />
              </FormField>
              <FormField label="End">
                <input
                  type="datetime-local"
                  className={inputClass}
                  required
                  value={shiftModal.shift.end}
                  onChange={(e) => setShiftModal({ ...shiftModal, shift: { ...shiftModal.shift, end: e.target.value } })}
                />
              </FormField>
            </div>
            <FormField label="Role / note">
              <input
                className={inputClass}
                placeholder="e.g. Office — reception, Field standby"
                value={shiftModal.shift.role_note}
                onChange={(e) => setShiftModal({ ...shiftModal, shift: { ...shiftModal.shift, role_note: e.target.value } })}
              />
            </FormField>
            <FormField label="Status">
              <select
                className={inputClass}
                value={shiftModal.shift.status}
                onChange={(e) => setShiftModal({ ...shiftModal, shift: { ...shiftModal.shift, status: e.target.value as Shift["status"] } })}
              >
                <option value="planned">Planned</option>
                <option value="confirmed">Confirmed</option>
                <option value="cancelled">Cancelled</option>
              </select>
            </FormField>
            <FormField label="Notes">
              <textarea
                className={inputClass}
                rows={2}
                value={shiftModal.shift.notes}
                onChange={(e) => setShiftModal({ ...shiftModal, shift: { ...shiftModal.shift, notes: e.target.value } })}
              />
            </FormField>
            <div className="mt-4 flex justify-between gap-2">
              {shiftModal.editingId ? (
                <button type="button" className="text-sm font-medium text-[var(--status-critical)] hover:underline" onClick={handleDeleteShift}>
                  Delete shift
                </button>
              ) : <span />}
              <div className="flex gap-2">
                <button type="button" className={btnSecondary} onClick={() => { setShiftModal(null); setShiftError(""); }}>Cancel</button>
                <button type="submit" disabled={saving} className={btnPrimary}>{saving ? "Saving…" : "Save"}</button>
              </div>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
