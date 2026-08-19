import { useEffect, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, filterSelectClass, btnPrimary, btnSecondary } from "../../components/Modal";
import {
  ROLE_LABELS,
  STAFF_ROLES,
  type AttendanceRecord,
  type LeaveRequest,
  type LeaveType,
  type Partner,
  type PayrollRun,
  type PayType,
  type Role,
  type StaffAccountEntry,
  type StaffProfile,
  type User,
} from "../../types";

type Tab = "attendance" | "leave" | "users" | "employees" | "payroll" | "partners";
type NewAction = { label: string; onClick: () => void } | null;

const PAY_TYPE_LABEL: Record<PayType, string> = {
  salary: "Monthly salary",
  hourly: "Hourly rate",
};

const LEAVE_TYPE_LABEL: Record<LeaveType, string> = {
  annual: "Annual leave",
  sick: "Sick leave",
  family_responsibility: "Family responsibility leave",
};

const LEAVE_BALANCE_FIELD: Record<LeaveType, "annual_leave_balance" | "sick_leave_balance" | "family_responsibility_leave_balance"> = {
  annual: "annual_leave_balance",
  sick: "sick_leave_balance",
  family_responsibility: "family_responsibility_leave_balance",
};

function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString("en-ZA", { hour: "2-digit", minute: "2-digit" });
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString();
}

export function StaffPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  // Partners (reselling) is a Management-level concern, same trust tier as
  // approving a customer deletion -- visible to Management too, not just
  // Admin, unlike Users/Employees/Payroll below.
  const isManagement = user?.role === "admin" || user?.role === "management";
  const [tab, setTab] = useState<Tab>("attendance");
  const [newAction, setNewAction] = useState<NewAction>(null);
  const [attendanceVersion, setAttendanceVersion] = useState(0);

  const TABS: { key: Tab; label: string }[] = [
    { key: "attendance", label: "Attendance Register" },
    { key: "leave", label: "Leave" },
    ...(isAdmin
      ? ([
          { key: "users", label: "Users" },
          { key: "employees", label: "Employees" },
          { key: "payroll", label: "Payroll" },
        ] as { key: Tab; label: string }[])
      : []),
    ...(isManagement ? ([{ key: "partners", label: "Partners" }] as { key: Tab; label: string }[]) : []),
  ];

  return (
    <div>
      <PageHeader
        title="Staff"
        subtitle="Attendance, overtime, and payroll for admin, support, and technician staff."
        actions={
          <>
            <ClockInOutWidget onChange={() => setAttendanceVersion((v) => v + 1)} />
            {newAction && (
              <button className={btnPrimary} onClick={newAction.onClick}>
                {newAction.label}
              </button>
            )}
          </>
        }
      />
      <div className="mb-4 flex gap-1 border-b border-[var(--border-hairline)]">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium ${
              tab === t.key
                ? "border-b-2 border-[var(--series-1)] text-[var(--text-primary)]"
                : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === "attendance" && (
        <AttendanceTab isAdmin={isAdmin} onRegisterNewAction={setNewAction} refreshSignal={attendanceVersion} />
      )}
      {tab === "leave" && <LeaveTab isAdmin={isAdmin} onRegisterNewAction={setNewAction} />}
      {tab === "users" && isAdmin && <UsersTab onRegisterNewAction={setNewAction} />}
      {tab === "employees" && isAdmin && <EmployeesTab onRegisterNewAction={setNewAction} />}
      {tab === "payroll" && isAdmin && <PayrollTab onRegisterNewAction={setNewAction} />}
      {tab === "partners" && isManagement && <PartnersTab onRegisterNewAction={setNewAction} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Clock in / out widget -- available to every staff member, in the header,
// regardless of which tab is active.
// ---------------------------------------------------------------------------

function ClockInOutWidget({ onChange }: { onChange?: () => void }) {
  const [open, setOpen] = useState<AttendanceRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function refresh() {
    api
      .get<AttendanceRecord | null>("/attendance/open/")
      .then((r) => setOpen(r.data))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleClick() {
    setBusy(true);
    setError("");
    try {
      if (open) {
        await api.post("/attendance/clock_out/");
      } else {
        await api.post("/attendance/clock_in/");
      }
      refresh();
      onChange?.();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail || "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return null;

  return (
    <div className="flex items-center gap-2">
      {error && <span className="text-xs text-[var(--status-critical)]">{error}</span>}
      <button
        type="button"
        onClick={handleClick}
        disabled={busy}
        className={
          open
            ? "rounded-md bg-[var(--status-critical)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            : btnPrimary
        }
      >
        {busy ? "…" : open ? `Clock out (in since ${formatTime(open.clock_in)})` : "Clock in"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Attendance register
// ---------------------------------------------------------------------------

const EMPTY_ATTENDANCE = { staff: "", date: "", clock_in: "", clock_out: "", notes: "" };

function AttendanceTab({
  isAdmin,
  onRegisterNewAction,
  refreshSignal,
}: {
  isAdmin: boolean;
  onRegisterNewAction: (action: NewAction) => void;
  refreshSignal: number;
}) {
  const [staffFilter, setStaffFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [staffList, setStaffList] = useState<User[]>([]);
  const { items, loading, refetch } = useApiList<AttendanceRecord>(
    `/attendance/?page_size=200${staffFilter ? `&staff=${staffFilter}` : ""}${
      dateFrom ? `&date_from=${dateFrom}` : ""
    }${dateTo ? `&date_to=${dateTo}` : ""}`
  );

  useEffect(() => {
    if (refreshSignal > 0) refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshSignal]);
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<AttendanceRecord | null>(null);
  const [form, setForm] = useState(EMPTY_ATTENDANCE);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (isAdmin) {
      api.get<{ results: User[] }>("/staff-users/?page_size=200").then((r) => setStaffList(r.data.results));
    }
  }, [isAdmin]);

  useEffect(() => {
    if (!isAdmin) {
      onRegisterNewAction(null);
      return;
    }
    onRegisterNewAction({
      label: "+ Add entry",
      onClick: () => {
        setEditing(null);
        setForm(EMPTY_ATTENDANCE);
        setError("");
        setShowModal(true);
      },
    });
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin]);

  function openEdit(record: AttendanceRecord) {
    setEditing(record);
    setForm({
      staff: String(record.staff),
      date: record.date,
      clock_in: record.clock_in.slice(0, 16),
      clock_out: record.clock_out ? record.clock_out.slice(0, 16) : "",
      notes: record.notes,
    });
    setError("");
    setShowModal(true);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      const payload = {
        staff: Number(form.staff),
        date: form.date,
        clock_in: form.clock_in,
        clock_out: form.clock_out || null,
        notes: form.notes,
      };
      if (editing) {
        await api.patch(`/attendance/${editing.id}/`, payload);
      } else {
        await api.post("/attendance/", payload);
      }
      setShowModal(false);
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: unknown } })?.response?.data;
      setError(
        typeof detail === "string"
          ? detail
          : (detail as { detail?: string; non_field_errors?: string[] })?.detail ||
              (detail as { non_field_errors?: string[] })?.non_field_errors?.[0] ||
              "Failed to save attendance record."
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(record: AttendanceRecord) {
    if (!confirm(`Delete this attendance record for ${record.staff_name} on ${record.date}?`)) return;
    await api.delete(`/attendance/${record.id}/`);
    refetch();
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {isAdmin && (
          <select className={filterSelectClass} value={staffFilter} onChange={(e) => setStaffFilter(e.target.value)}>
            <option value="">All staff</option>
            {staffList.map((s) => (
              <option key={s.id} value={s.id}>
                {s.first_name || s.username} {s.last_name}
              </option>
            ))}
          </select>
        )}
        <FormField label="From">
          <input type="date" className={inputClass} value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        </FormField>
        <FormField label="To">
          <input type="date" className={inputClass} value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        </FormField>
        {(staffFilter || dateFrom || dateTo) && (
          <button
            type="button"
            className={`${btnSecondary} mb-3`}
            onClick={() => {
              setStaffFilter("");
              setDateFrom("");
              setDateTo("");
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
              <TH>Date</TH>
              {isAdmin && <TH>Staff</TH>}
              <TH>Clock in</TH>
              <TH>Clock out</TH>
              <TH>Worked hours</TH>
              <TH>Overtime</TH>
              <TH>Notes</TH>
              {isAdmin && <TH></TH>}
            </tr>
          </THead>
          <tbody>
            {items.map((r) => (
              <TR key={r.id}>
                <TD>{formatDate(r.date)}</TD>
                {isAdmin && <TD className="font-medium">{r.staff_name}</TD>}
                <TD>{formatTime(r.clock_in)}</TD>
                <TD>{r.clock_out ? formatTime(r.clock_out) : "—"}</TD>
                <TD className="tabular-nums">{r.worked_hours}</TD>
                <TD className="tabular-nums">
                  {Number(r.overtime_hours) > 0 ? (
                    <span className="font-medium text-[var(--status-warning)]">{r.overtime_hours}</span>
                  ) : (
                    "0.00"
                  )}
                </TD>
                <TD>{r.notes || "—"}</TD>
                {isAdmin && (
                  <TD>
                    <button className="text-xs text-[var(--series-1)] hover:underline" onClick={() => openEdit(r)}>
                      Edit
                    </button>
                    <button
                      className="ml-2 text-xs text-[var(--status-critical)] hover:underline"
                      onClick={() => handleDelete(r)}
                    >
                      Delete
                    </button>
                  </TD>
                )}
              </TR>
            ))}
            {items.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]" >No attendance records match the current filters.</TD>
              </TR>
            )}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title={editing ? "Edit attendance record" : "Add attendance record"} onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Staff member">
              <select
                className={inputClass}
                required
                value={form.staff}
                onChange={(e) => setForm({ ...form, staff: e.target.value })}
              >
                <option value="">Select staff…</option>
                {staffList.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.first_name || s.username} {s.last_name}
                  </option>
                ))}
              </select>
            </FormField>
            <FormField label="Date">
              <input
                type="date"
                className={inputClass}
                required
                value={form.date}
                onChange={(e) => setForm({ ...form, date: e.target.value })}
              />
            </FormField>
            <FormField label="Clock in">
              <input
                type="datetime-local"
                className={inputClass}
                required
                value={form.clock_in}
                onChange={(e) => setForm({ ...form, clock_in: e.target.value })}
              />
            </FormField>
            <FormField label="Clock out (optional)">
              <input
                type="datetime-local"
                className={inputClass}
                value={form.clock_out}
                onChange={(e) => setForm({ ...form, clock_out: e.target.value })}
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
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Leave (annual / sick / family responsibility)
// ---------------------------------------------------------------------------

const EMPTY_LEAVE = { staff: "", leave_type: "annual" as LeaveType, start_date: "", end_date: "", reason: "" };

function LeaveTab({
  isAdmin,
  onRegisterNewAction,
}: {
  isAdmin: boolean;
  onRegisterNewAction: (action: NewAction) => void;
}) {
  const [staffFilter, setStaffFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [staffList, setStaffList] = useState<User[]>([]);
  const [ownProfile, setOwnProfile] = useState<StaffProfile | null>(null);
  const { items, loading, refetch } = useApiList<LeaveRequest>(
    `/leave-requests/?page_size=200${staffFilter ? `&staff=${staffFilter}` : ""}${
      typeFilter ? `&leave_type=${typeFilter}` : ""
    }${statusFilter ? `&status=${statusFilter}` : ""}`
  );
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState(EMPTY_LEAVE);
  const [attachment, setAttachment] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [rejecting, setRejecting] = useState<LeaveRequest | null>(null);
  const [decisionNote, setDecisionNote] = useState("");

  useEffect(() => {
    if (isAdmin) {
      api.get<{ results: User[] }>("/staff-users/?page_size=200").then((r) => setStaffList(r.data.results));
    } else {
      api.get<StaffProfile | null>("/staff-profiles/me/").then((r) => setOwnProfile(r.data));
    }
  }, [isAdmin]);

  useEffect(() => {
    onRegisterNewAction({
      label: "+ Request leave",
      onClick: () => {
        setForm(EMPTY_LEAVE);
        setAttachment(null);
        setError("");
        setShowModal(true);
      },
    });
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      const fd = new FormData();
      if (isAdmin) fd.append("staff", form.staff);
      fd.append("leave_type", form.leave_type);
      fd.append("start_date", form.start_date);
      fd.append("end_date", form.end_date);
      fd.append("reason", form.reason);
      if (attachment) fd.append("attachment", attachment);
      await api.post("/leave-requests/", fd, { headers: { "Content-Type": "multipart/form-data" } });
      setShowModal(false);
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: unknown } })?.response?.data;
      setError(
        typeof detail === "string"
          ? detail
          : (detail as { non_field_errors?: string[] })?.non_field_errors?.[0] || "Failed to submit leave request."
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleWithdraw(leave: LeaveRequest) {
    if (!confirm(`Withdraw this ${LEAVE_TYPE_LABEL[leave.leave_type].toLowerCase()} request?`)) return;
    await api.delete(`/leave-requests/${leave.id}/`);
    refetch();
  }

  async function handleApprove(leave: LeaveRequest) {
    if (!confirm(`Approve ${leave.days_requested} day(s) of ${LEAVE_TYPE_LABEL[leave.leave_type].toLowerCase()} for ${leave.staff_name}?`))
      return;
    setBusyId(leave.id);
    try {
      await api.post(`/leave-requests/${leave.id}/approve/`);
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      alert(detail || "Failed to approve this request.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleReject(e: FormEvent) {
    e.preventDefault();
    if (!rejecting) return;
    setBusyId(rejecting.id);
    try {
      await api.post(`/leave-requests/${rejecting.id}/reject/`, { decision_note: decisionNote });
      setRejecting(null);
      setDecisionNote("");
      refetch();
    } finally {
      setBusyId(null);
    }
  }

  const ownBalance = form.leave_type && ownProfile ? ownProfile[LEAVE_BALANCE_FIELD[form.leave_type]] : null;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {isAdmin && (
          <select className={filterSelectClass} value={staffFilter} onChange={(e) => setStaffFilter(e.target.value)}>
            <option value="">All staff</option>
            {staffList.map((s) => (
              <option key={s.id} value={s.id}>
                {s.first_name || s.username} {s.last_name}
              </option>
            ))}
          </select>
        )}
        <select className={filterSelectClass} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          <option value="">All leave types</option>
          {Object.entries(LEAVE_TYPE_LABEL).map(([v, l]) => (
            <option key={v} value={v}>{l}</option>
          ))}
        </select>
        <select className={filterSelectClass} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          <option value="pending">Pending</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
        </select>
        {(staffFilter || typeFilter || statusFilter) && (
          <button
            type="button"
            className={btnSecondary}
            onClick={() => {
              setStaffFilter("");
              setTypeFilter("");
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
              {isAdmin && <TH>Staff</TH>}
              <TH>Type</TH>
              <TH>Dates</TH>
              <TH>Days</TH>
              <TH>Reason</TH>
              <TH>Letter</TH>
              <TH>Status</TH>
              <TH>Decided by</TH>
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((l) => (
              <TR key={l.id}>
                {isAdmin && <TD className="font-medium">{l.staff_name}</TD>}
                <TD>{LEAVE_TYPE_LABEL[l.leave_type]}</TD>
                <TD>
                  {formatDate(l.start_date)} – {formatDate(l.end_date)}
                </TD>
                <TD className="tabular-nums">{l.days_requested}</TD>
                <TD>{l.reason || "—"}</TD>
                <TD>
                  {l.attachment ? (
                    <a href={l.attachment} target="_blank" rel="noreferrer" className="text-[var(--series-1)] hover:underline">
                      View
                    </a>
                  ) : (
                    "—"
                  )}
                </TD>
                <TD>
                  <StatusBadge status={l.status} />
                  {l.status === "rejected" && l.decision_note && (
                    <div className="mt-1 text-xs text-[var(--text-muted)]">{l.decision_note}</div>
                  )}
                </TD>
                <TD>{l.decided_by_name || "—"}</TD>
                <TD>
                  {l.status === "pending" && isAdmin && (
                    <>
                      <button
                        className="text-xs text-[var(--series-1)] hover:underline"
                        disabled={busyId === l.id}
                        onClick={() => handleApprove(l)}
                      >
                        Approve
                      </button>
                      <button
                        className="ml-2 text-xs text-[var(--status-critical)] hover:underline"
                        disabled={busyId === l.id}
                        onClick={() => {
                          setRejecting(l);
                          setDecisionNote("");
                        }}
                      >
                        Reject
                      </button>
                    </>
                  )}
                  {l.status === "pending" && (
                    <button
                      className="ml-2 text-xs text-[var(--status-critical)] hover:underline"
                      onClick={() => handleWithdraw(l)}
                    >
                      Withdraw
                    </button>
                  )}
                </TD>
              </TR>
            ))}
            {items.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]">No leave requests match the current filters.</TD>
              </TR>
            )}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title="Request leave" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            {isAdmin && (
              <FormField label="Staff member">
                <select
                  className={inputClass}
                  required
                  value={form.staff}
                  onChange={(e) => setForm({ ...form, staff: e.target.value })}
                >
                  <option value="">Select staff…</option>
                  {staffList.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.first_name || s.username} {s.last_name}
                    </option>
                  ))}
                </select>
              </FormField>
            )}
            <FormField label="Leave type">
              <select
                className={inputClass}
                value={form.leave_type}
                onChange={(e) => setForm({ ...form, leave_type: e.target.value as LeaveType })}
              >
                {Object.entries(LEAVE_TYPE_LABEL).map(([v, l]) => (
                  <option key={v} value={v}>{l}</option>
                ))}
              </select>
            </FormField>
            {ownBalance != null && (
              <p className="mb-3 text-xs text-[var(--text-muted)]">
                You have {ownBalance} day(s) of {LEAVE_TYPE_LABEL[form.leave_type].toLowerCase()} remaining.
              </p>
            )}
            <FormField label="Start date">
              <input
                type="date"
                className={inputClass}
                required
                value={form.start_date}
                onChange={(e) => setForm({ ...form, start_date: e.target.value })}
              />
            </FormField>
            <FormField label="End date">
              <input
                type="date"
                className={inputClass}
                required
                value={form.end_date}
                onChange={(e) => setForm({ ...form, end_date: e.target.value })}
              />
            </FormField>
            <FormField label="Reason (optional)">
              <input
                className={inputClass}
                value={form.reason}
                onChange={(e) => setForm({ ...form, reason: e.target.value })}
              />
            </FormField>
            {form.leave_type === "sick" && (
              <FormField label="Sick leave letter / certificate (optional)">
                <input
                  type="file"
                  className={inputClass}
                  onChange={(e) => setAttachment(e.target.files?.[0] ?? null)}
                />
              </FormField>
            )}
            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" className={btnPrimary} disabled={saving}>
                {saving ? "Submitting…" : "Submit request"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {rejecting && (
        <Modal title="Reject leave request" onClose={() => setRejecting(null)}>
          <form onSubmit={handleReject}>
            <p className="mb-3 text-sm text-[var(--text-secondary)]">
              Rejecting {rejecting.staff_name}'s {LEAVE_TYPE_LABEL[rejecting.leave_type].toLowerCase()} request (
              {formatDate(rejecting.start_date)} – {formatDate(rejecting.end_date)}).
            </p>
            <FormField label="Reason (optional)">
              <input
                className={inputClass}
                value={decisionNote}
                onChange={(e) => setDecisionNote(e.target.value)}
              />
            </FormField>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setRejecting(null)}>
                Cancel
              </button>
              <button type="submit" className={btnPrimary} disabled={busyId === rejecting.id}>
                {busyId === rejecting.id ? "Rejecting…" : "Reject request"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Users (staff account lifecycle -- create, edit, suspend/reactivate,
// permanently delete). Admin-only, separate from Configs -> Permissions
// (which only manages section access on an existing account).
// ---------------------------------------------------------------------------

const EMPTY_USER_FORM = {
  username: "",
  password: "",
  first_name: "",
  last_name: "",
  email: "",
  phone: "",
  role: "support" as Exclude<Role, "customer">,
  is_active: true,
};

function UsersTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const { user: currentUser } = useAuth();
  const { items, loading, refetch } = useApiList<StaffAccountEntry>("/staff-accounts/?page_size=200");
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<StaffAccountEntry | null>(null);
  const [form, setForm] = useState(EMPTY_USER_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState<StaffAccountEntry | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [toggleBusyId, setToggleBusyId] = useState<number | null>(null);
  const [resetBusyId, setResetBusyId] = useState<number | null>(null);

  useEffect(() => {
    onRegisterNewAction({
      label: "+ Add user",
      onClick: () => {
        setEditing(null);
        setForm(EMPTY_USER_FORM);
        setError("");
        setShowModal(true);
      },
    });
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function openEdit(entry: StaffAccountEntry) {
    setEditing(entry);
    setForm({
      username: entry.username,
      password: "",
      first_name: entry.first_name,
      last_name: entry.last_name,
      email: entry.email,
      phone: entry.phone,
      role: entry.role as Exclude<Role, "customer">,
      is_active: entry.is_active,
    });
    setError("");
    setShowModal(true);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        username: form.username,
        first_name: form.first_name,
        last_name: form.last_name,
        email: form.email,
        phone: form.phone,
        role: form.role,
        is_active: form.is_active,
      };
      // Blank password on an edit means "leave it unchanged" -- only send
      // it when the admin actually typed a new one (always sent, and
      // always required, when creating a brand-new account).
      if (form.password) payload.password = form.password;
      if (editing) {
        await api.patch(`/staff-accounts/${editing.id}/`, payload);
      } else {
        await api.post("/staff-accounts/", payload);
      }
      setShowModal(false);
      refetch();
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setError(typeof firstError === "string" ? firstError : "Could not save this user — please try again.");
    } finally {
      setSaving(false);
    }
  }

  async function handleToggleActive(entry: StaffAccountEntry) {
    setToggleBusyId(entry.id);
    try {
      await api.patch(`/staff-accounts/${entry.id}/`, { is_active: !entry.is_active });
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: { is_active?: string[] } } })?.response?.data?.is_active;
      alert(detail?.[0] || "Could not update this account.");
    } finally {
      setToggleBusyId(null);
    }
  }

  async function handleSendResetLink(entry: StaffAccountEntry) {
    setResetBusyId(entry.id);
    try {
      const res = await api.post<{ detail: string }>(`/staff-accounts/${entry.id}/send_reset_link/`);
      alert(res.data.detail);
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      alert(detail || "Could not send the reset link.");
    } finally {
      setResetBusyId(null);
    }
  }

  async function handleSuspendFromDeleteModal() {
    if (!deleting) return;
    setDeleteBusy(true);
    try {
      await api.patch(`/staff-accounts/${deleting.id}/`, { is_active: false });
      setDeleting(null);
      refetch();
    } catch {
      alert("Could not suspend this account.");
    } finally {
      setDeleteBusy(false);
    }
  }

  async function handleDeleteConfirm() {
    if (!deleting) return;
    setDeleteBusy(true);
    try {
      await api.delete(`/staff-accounts/${deleting.id}/`);
      setDeleting(null);
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      alert(detail || "Could not delete this account.");
    } finally {
      setDeleteBusy(false);
    }
  }

  return (
    <div>
      <p className="mb-3 text-sm text-[var(--text-secondary)]">
        Create and manage staff logins. Suspending blocks sign-in but keeps their history intact and reversible;
        deleting permanently removes the account and can't be undone.
      </p>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Name</TH>
              <TH>Username</TH>
              <TH>Email</TH>
              <TH>Phone</TH>
              <TH>Role</TH>
              <TH>Status</TH>
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((entry) => {
              const isSelf = currentUser?.id === entry.id;
              return (
                <TR key={entry.id}>
                  <TD className="font-medium">
                    {`${entry.first_name} ${entry.last_name}`.trim() || entry.username}
                    {isSelf && <span className="ml-1 text-xs text-[var(--text-muted)]">(you)</span>}
                  </TD>
                  <TD className="text-[var(--text-secondary)]">{entry.username}</TD>
                  <TD className="text-[var(--text-secondary)]">{entry.email || "—"}</TD>
                  <TD className="text-[var(--text-secondary)]">{entry.phone || "—"}</TD>
                  <TD>{ROLE_LABELS[entry.role]}</TD>
                  <TD><StatusBadge status={entry.is_active ? "active" : "inactive"} /></TD>
                  <TD>
                    <button className="text-xs text-[var(--series-1)] hover:underline" onClick={() => openEdit(entry)}>
                      Edit
                    </button>
                    {!isSelf && (
                      <>
                        <button
                          className="ml-2 text-xs text-[var(--series-1)] hover:underline disabled:opacity-50"
                          disabled={resetBusyId === entry.id || !entry.email}
                          title={entry.email ? undefined : "Add an email address (via Edit) before sending a reset link"}
                          onClick={() => handleSendResetLink(entry)}
                        >
                          {resetBusyId === entry.id ? "Sending…" : "Send reset link"}
                        </button>
                        <button
                          className="ml-2 text-xs text-[var(--series-1)] hover:underline"
                          disabled={toggleBusyId === entry.id}
                          onClick={() => handleToggleActive(entry)}
                        >
                          {entry.is_active ? "Suspend" : "Reactivate"}
                        </button>
                        <button
                          className="ml-2 text-xs text-[var(--status-critical)] hover:underline"
                          onClick={() => setDeleting(entry)}
                        >
                          Delete
                        </button>
                      </>
                    )}
                  </TD>
                </TR>
              );
            })}
            {items.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]">No staff accounts yet.</TD>
              </TR>
            )}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title={editing ? "Edit user" : "Add user"} onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit} autoComplete="off">
            <FormField label="Username">
              <input
                className={inputClass}
                required
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
                autoComplete="off"
                name="staff-username"
              />
            </FormField>
            <FormField label={editing ? "New password (leave blank to keep current)" : "Password"}>
              <input
                type="password"
                className={inputClass}
                required={!editing}
                minLength={8}
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                autoComplete="new-password"
                name="staff-password"
              />
            </FormField>
            <div className="grid grid-cols-2 gap-2">
              <FormField label="First name">
                <input
                  className={inputClass}
                  required
                  value={form.first_name}
                  onChange={(e) => setForm({ ...form, first_name: e.target.value })}
                />
              </FormField>
              <FormField label="Last name">
                <input
                  className={inputClass}
                  required
                  value={form.last_name}
                  onChange={(e) => setForm({ ...form, last_name: e.target.value })}
                />
              </FormField>
            </div>
            <FormField label="Email">
              <input
                type="email"
                className={inputClass}
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />
            </FormField>
            <FormField label="Phone">
              <input
                className={inputClass}
                value={form.phone}
                onChange={(e) => setForm({ ...form, phone: e.target.value })}
              />
            </FormField>
            <FormField label="Role">
              <select
                className={inputClass}
                value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value as Exclude<Role, "customer"> })}
              >
                {STAFF_ROLES.map((r) => (
                  <option key={r} value={r}>{ROLE_LABELS[r]}</option>
                ))}
              </select>
            </FormField>
            {editing && (
              <label className="mb-3 flex items-center gap-2 text-sm text-[var(--text-primary)]">
                <input
                  type="checkbox"
                  disabled={currentUser?.id === editing.id}
                  checked={form.is_active}
                  onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
                />
                Active (can sign in)
              </label>
            )}
            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" className={btnPrimary} disabled={saving}>
                {saving ? "Saving…" : editing ? "Save changes" : "Create user"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {deleting && (
        <Modal title={`Delete ${`${deleting.first_name} ${deleting.last_name}`.trim() || deleting.username}?`} onClose={() => setDeleting(null)}>
          <p className="mb-4 text-sm text-[var(--text-secondary)]">
            This permanently removes the account, along with their attendance records, leave requests, payroll
            history, and any shifts assigned to them. This can't be undone. If you just want to stop them from
            signing in — while keeping their history intact — suspend the account instead.
          </p>
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={() => setDeleting(null)}>
              Cancel
            </button>
            <button type="button" className={btnSecondary} disabled={deleteBusy} onClick={handleSuspendFromDeleteModal}>
              {deleteBusy ? "Working…" : "Suspend instead"}
            </button>
            <button
              type="button"
              className="rounded-md bg-[var(--status-critical)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
              disabled={deleteBusy}
              onClick={handleDeleteConfirm}
            >
              {deleteBusy ? "Deleting…" : "Delete permanently"}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Partners (reselling) -- Management/Admin
// ---------------------------------------------------------------------------

const EMPTY_PARTNER_FORM = {
  name: "",
  contact_person: "",
  email: "",
  phone: "",
  commission_rate: "",
  notes: "",
  is_active: true,
};

function PartnersTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const { items, loading, refetch } = useApiList<Partner>("/partners/?page_size=200&ordering=name");
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<Partner | null>(null);
  const [form, setForm] = useState(EMPTY_PARTNER_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState<Partner | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  useEffect(() => {
    onRegisterNewAction({
      label: "+ Add partner",
      onClick: () => {
        setEditing(null);
        setForm(EMPTY_PARTNER_FORM);
        setError("");
        setShowModal(true);
      },
    });
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function openEdit(entry: Partner) {
    setEditing(entry);
    setForm({
      name: entry.name,
      contact_person: entry.contact_person,
      email: entry.email,
      phone: entry.phone,
      commission_rate: entry.commission_rate ?? "",
      notes: entry.notes,
      is_active: entry.is_active,
    });
    setError("");
    setShowModal(true);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        name: form.name,
        contact_person: form.contact_person,
        email: form.email,
        phone: form.phone,
        commission_rate: form.commission_rate ? form.commission_rate : null,
        notes: form.notes,
        is_active: form.is_active,
      };
      if (editing) {
        await api.patch(`/partners/${editing.id}/`, payload);
      } else {
        await api.post("/partners/", payload);
      }
      setShowModal(false);
      refetch();
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setError(typeof firstError === "string" ? firstError : "Could not save this partner — please try again.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDeleteConfirm() {
    if (!deleting) return;
    setDeleteBusy(true);
    setDeleteError("");
    try {
      await api.delete(`/partners/${deleting.id}/`);
      setDeleting(null);
      refetch();
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setDeleteError(typeof firstError === "string" ? firstError : "Could not delete this partner.");
    } finally {
      setDeleteBusy(false);
    }
  }

  return (
    <div>
      <p className="mb-3 text-sm text-[var(--text-secondary)]">
        Reseller partners customers can be tagged to under Customers. Deleting a partner doesn't delete its
        customers -- they just become direct (no-partner) customers. To control which staff can see which
        partners' customers, go to Configs → Permissions.
      </p>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Name</TH>
              <TH>Contact</TH>
              <TH>Commission</TH>
              <TH>Customers</TH>
              <TH>Status</TH>
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((p) => (
              <TR key={p.id}>
                <TD className="font-medium">{p.name}</TD>
                <TD>
                  <div>{p.contact_person || "—"}</div>
                  <div className="text-xs text-[var(--text-muted)]">{p.email}{p.email && p.phone ? " · " : ""}{p.phone}</div>
                </TD>
                <TD>{p.commission_rate ? `${p.commission_rate}%` : "—"}</TD>
                <TD>{p.customer_count}</TD>
                <TD><StatusBadge status={p.is_active ? "active" : "inactive"} /></TD>
                <TD>
                  <div className="flex gap-2">
                    <button type="button" className={btnSecondary} onClick={() => openEdit(p)}>Edit</button>
                    <button
                      type="button"
                      className="text-xs font-medium text-[var(--status-critical)] hover:underline"
                      onClick={() => {
                        setDeleting(p);
                        setDeleteError("");
                      }}
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
        <Modal title={editing ? `Edit ${editing.name}` : "New partner"} onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Name">
              <input className={inputClass} required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </FormField>
            <FormField label="Contact person">
              <input className={inputClass} value={form.contact_person} onChange={(e) => setForm({ ...form, contact_person: e.target.value })} />
            </FormField>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <FormField label="Email">
                <input type="email" className={inputClass} value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
              </FormField>
              <FormField label="Phone">
                <input className={inputClass} value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
              </FormField>
            </div>
            <FormField label="Commission rate % (optional)">
              <input
                type="number"
                step="0.01"
                className={inputClass}
                value={form.commission_rate}
                onChange={(e) => setForm({ ...form, commission_rate: e.target.value })}
              />
            </FormField>
            <FormField label="Notes">
              <textarea className={inputClass} rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
            </FormField>
            <label className="mb-4 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
              <span>Active</span>
            </label>

            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>Cancel</button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : editing ? "Save changes" : "Create partner"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {deleting && (
        <Modal title={`Delete ${deleting.name}?`} onClose={() => setDeleting(null)}>
          <p className="mb-3 text-sm text-[var(--text-secondary)]">
            {deleting.customer_count > 0
              ? `This partner has ${deleting.customer_count} customer${deleting.customer_count === 1 ? "" : "s"} tagged to it. They won't be deleted -- they'll just become direct (no-partner) customers.`
              : "This partner has no customers tagged to it."}
          </p>
          {deleteError && <p className="mb-3 text-sm text-[var(--status-critical)]">{deleteError}</p>}
          <div className="flex justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={() => setDeleting(null)}>Cancel</button>
            <button type="button" disabled={deleteBusy} className={btnPrimary} onClick={handleDeleteConfirm}>
              {deleteBusy ? "Deleting…" : "Delete partner"}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Employees (staff pay configuration)
// ---------------------------------------------------------------------------

const EMPTY_PROFILE_FORM = {
  user: "",
  id_number: "",
  license_number: "",
  pay_type: "hourly" as PayType,
  monthly_salary: "",
  hourly_rate: "",
  standard_daily_hours: "8.00",
  overtime_multiplier: "1.50",
  annual_leave_balance: "21.0",
  sick_leave_balance: "30.0",
  family_responsibility_leave_balance: "3.0",
  is_active: true,
};

function EmployeesTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const { items, loading, refetch } = useApiList<StaffProfile>("/staff-profiles/?page_size=200");
  const [staffList, setStaffList] = useState<User[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<StaffProfile | null>(null);
  const [form, setForm] = useState(EMPTY_PROFILE_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get<{ results: User[] }>("/staff-users/?page_size=200").then((r) => setStaffList(r.data.results));
  }, []);

  useEffect(() => {
    onRegisterNewAction({
      label: "+ Add employee",
      onClick: () => {
        setEditing(null);
        setForm(EMPTY_PROFILE_FORM);
        setError("");
        setShowModal(true);
      },
    });
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const eligibleStaff = staffList.filter((s) => !items.some((p) => p.user === s.id));

  function openEdit(profile: StaffProfile) {
    setEditing(profile);
    setForm({
      user: String(profile.user),
      id_number: profile.id_number,
      license_number: profile.license_number,
      pay_type: profile.pay_type,
      monthly_salary: profile.monthly_salary || "",
      hourly_rate: profile.hourly_rate || "",
      standard_daily_hours: profile.standard_daily_hours,
      overtime_multiplier: profile.overtime_multiplier,
      annual_leave_balance: profile.annual_leave_balance,
      sick_leave_balance: profile.sick_leave_balance,
      family_responsibility_leave_balance: profile.family_responsibility_leave_balance,
      is_active: profile.is_active,
    });
    setError("");
    setShowModal(true);
  }

  async function handleDelete(profile: StaffProfile) {
    if (
      !confirm(
        `Remove ${profile.staff_name} (${profile.employee_number}) from payroll? Their past attendance and payroll history are kept, but they'll be excluded from future payroll runs until re-added.`
      )
    )
      return;
    await api.delete(`/staff-profiles/${profile.id}/`);
    refetch();
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      const payload = {
        user: Number(form.user),
        id_number: form.id_number,
        license_number: form.license_number,
        pay_type: form.pay_type,
        monthly_salary: form.pay_type === "salary" ? form.monthly_salary || null : null,
        hourly_rate: form.pay_type === "hourly" ? form.hourly_rate || null : null,
        standard_daily_hours: form.standard_daily_hours,
        overtime_multiplier: form.overtime_multiplier,
        annual_leave_balance: form.annual_leave_balance,
        sick_leave_balance: form.sick_leave_balance,
        family_responsibility_leave_balance: form.family_responsibility_leave_balance,
        is_active: form.is_active,
      };
      if (editing) {
        await api.patch(`/staff-profiles/${editing.id}/`, payload);
      } else {
        await api.post("/staff-profiles/", payload);
      }
      setShowModal(false);
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: unknown } })?.response?.data;
      setError(
        typeof detail === "string"
          ? detail
          : (detail as { non_field_errors?: string[] })?.non_field_errors?.[0] ||
              JSON.stringify(detail) ||
              "Failed to save employee."
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Employee #</TH>
              <TH>Name</TH>
              <TH>ID number</TH>
              <TH>License number</TH>
              <TH>Contact number</TH>
              <TH>Role</TH>
              <TH>Pay type</TH>
              <TH>Rate</TH>
              <TH>Daily hours</TH>
              <TH>OT multiplier</TH>
              <TH>Leave balance (Annual/Sick/Family)</TH>
              <TH>Status</TH>
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((p) => (
              <TR key={p.id}>
                <TD className="font-medium">{p.employee_number}</TD>
                <TD>{p.staff_name}</TD>
                <TD>{p.id_number || "—"}</TD>
                <TD>{p.license_number || "—"}</TD>
                <TD>{p.phone || "—"}</TD>
                <TD className="capitalize">{p.role}</TD>
                <TD>{PAY_TYPE_LABEL[p.pay_type]}</TD>
                <TD className="tabular-nums">
                  {p.pay_type === "salary" ? `R ${p.monthly_salary}/mo` : `R ${p.hourly_rate}/hr`}
                </TD>
                <TD className="tabular-nums">{p.standard_daily_hours}</TD>
                <TD className="tabular-nums">{p.overtime_multiplier}x</TD>
                <TD className="tabular-nums">
                  {p.annual_leave_balance} / {p.sick_leave_balance} / {p.family_responsibility_leave_balance}
                </TD>
                <TD>
                  <StatusBadge status={p.is_active ? "active" : "inactive"} />
                </TD>
                <TD>
                  <button className="text-xs text-[var(--series-1)] hover:underline" onClick={() => openEdit(p)}>
                    Edit
                  </button>
                  <button
                    className="ml-2 text-xs text-[var(--status-critical)] hover:underline"
                    onClick={() => handleDelete(p)}
                  >
                    Delete
                  </button>
                </TD>
              </TR>
            ))}
            {items.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]">No employees configured for payroll yet.</TD>
              </TR>
            )}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title={editing ? "Edit employee" : "Add employee"} onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            {!editing && (
              <FormField label="Staff member">
                <select
                  className={inputClass}
                  required
                  value={form.user}
                  onChange={(e) => setForm({ ...form, user: e.target.value })}
                >
                  <option value="">Select staff…</option>
                  {eligibleStaff.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.first_name || s.username} {s.last_name} ({s.role})
                    </option>
                  ))}
                </select>
              </FormField>
            )}
            <FormField label="ID number">
              <input
                className={inputClass}
                placeholder="National ID / passport number"
                value={form.id_number}
                onChange={(e) => setForm({ ...form, id_number: e.target.value })}
              />
            </FormField>
            <FormField label="License number">
              <input
                className={inputClass}
                placeholder="Driver's license number"
                value={form.license_number}
                onChange={(e) => setForm({ ...form, license_number: e.target.value })}
              />
            </FormField>
            <FormField label="Pay type">
              <select
                className={inputClass}
                value={form.pay_type}
                onChange={(e) => setForm({ ...form, pay_type: e.target.value as PayType })}
              >
                <option value="hourly">Hourly rate</option>
                <option value="salary">Monthly salary</option>
              </select>
            </FormField>
            {form.pay_type === "salary" ? (
              <FormField label="Monthly salary (R)">
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  className={inputClass}
                  required
                  value={form.monthly_salary}
                  onChange={(e) => setForm({ ...form, monthly_salary: e.target.value })}
                />
              </FormField>
            ) : (
              <FormField label="Hourly rate (R)">
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  className={inputClass}
                  required
                  value={form.hourly_rate}
                  onChange={(e) => setForm({ ...form, hourly_rate: e.target.value })}
                />
              </FormField>
            )}
            <FormField label="Standard hours per day (before overtime)">
              <input
                type="number"
                step="0.25"
                min="1"
                className={inputClass}
                required
                value={form.standard_daily_hours}
                onChange={(e) => setForm({ ...form, standard_daily_hours: e.target.value })}
              />
            </FormField>
            <FormField label="Overtime rate multiplier">
              <input
                type="number"
                step="0.05"
                min="1"
                className={inputClass}
                required
                value={form.overtime_multiplier}
                onChange={(e) => setForm({ ...form, overtime_multiplier: e.target.value })}
              />
            </FormField>
            <div className="mb-1 mt-2 text-sm font-medium text-[var(--text-secondary)]">Leave balances (days)</div>
            <div className="mb-3 grid grid-cols-3 gap-2">
              <FormField label="Annual">
                <input
                  type="number"
                  step="0.5"
                  min="0"
                  className={inputClass}
                  required
                  value={form.annual_leave_balance}
                  onChange={(e) => setForm({ ...form, annual_leave_balance: e.target.value })}
                />
              </FormField>
              <FormField label="Sick">
                <input
                  type="number"
                  step="0.5"
                  min="0"
                  className={inputClass}
                  required
                  value={form.sick_leave_balance}
                  onChange={(e) => setForm({ ...form, sick_leave_balance: e.target.value })}
                />
              </FormField>
              <FormField label="Family resp.">
                <input
                  type="number"
                  step="0.5"
                  min="0"
                  className={inputClass}
                  required
                  value={form.family_responsibility_leave_balance}
                  onChange={(e) => setForm({ ...form, family_responsibility_leave_balance: e.target.value })}
                />
              </FormField>
            </div>
            <label className="mb-3 flex items-center gap-2 text-sm text-[var(--text-primary)]">
              <input
                type="checkbox"
                checked={form.is_active}
                onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
              />
              Active (included in payroll runs)
            </label>
            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" className={btnPrimary} disabled={saving}>
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Payroll runs
// ---------------------------------------------------------------------------

function PayrollTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const { items, loading, refetch } = useApiList<PayrollRun>("/payroll-runs/?page_size=100");
  const [showModal, setShowModal] = useState(false);
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<PayrollRun | null>(null);
  const [busyAction, setBusyAction] = useState(false);

  useEffect(() => {
    onRegisterNewAction({
      label: "+ New payroll run",
      onClick: () => {
        setPeriodStart("");
        setPeriodEnd("");
        setError("");
        setShowModal(true);
      },
    });
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      const res = await api.post<PayrollRun>("/payroll-runs/", {
        period_start: periodStart,
        period_end: periodEnd,
      });
      setShowModal(false);
      refetch();
      setSelected(res.data);
    } catch (err) {
      const detail = (err as { response?: { data?: unknown } })?.response?.data;
      setError(
        typeof detail === "string"
          ? detail
          : (detail as { non_field_errors?: string[] })?.non_field_errors?.[0] || "Failed to generate payroll run."
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleRecalculate(run: PayrollRun) {
    setBusyAction(true);
    try {
      const res = await api.post<PayrollRun>(`/payroll-runs/${run.id}/recalculate/`);
      setSelected(res.data);
      refetch();
    } finally {
      setBusyAction(false);
    }
  }

  async function handleFinalize(run: PayrollRun) {
    if (!confirm("Finalize this payroll run? It can no longer be recalculated afterwards.")) return;
    setBusyAction(true);
    try {
      const res = await api.post<PayrollRun>(`/payroll-runs/${run.id}/finalize/`);
      setSelected(res.data);
      refetch();
    } finally {
      setBusyAction(false);
    }
  }

  async function handleExport(run: PayrollRun) {
    const res = await api.get(`/payroll-runs/${run.id}/export_csv/`, { responseType: "blob" });
    const url = window.URL.createObjectURL(new Blob([res.data]));
    const link = document.createElement("a");
    link.href = url;
    link.download = `payroll_${run.period_start}_${run.period_end}.csv`;
    link.click();
    window.URL.revokeObjectURL(url);
  }

  async function handleDelete(run: PayrollRun) {
    if (!confirm(`Delete the payroll run for ${formatDate(run.period_start)} – ${formatDate(run.period_end)}?`)) return;
    setBusyAction(true);
    try {
      await api.delete(`/payroll-runs/${run.id}/`);
      if (selected?.id === run.id) setSelected(null);
      refetch();
    } finally {
      setBusyAction(false);
    }
  }

  return (
    <div>
      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Period</TH>
              <TH>Status</TH>
              <TH>Staff</TH>
              <TH>Regular hours</TH>
              <TH>Overtime hours</TH>
              <TH>Gross pay</TH>
              <TH>Created</TH>
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((run) => (
              <TR key={run.id} onClick={() => setSelected(run)}>
                <TD className="font-medium">
                  {formatDate(run.period_start)} – {formatDate(run.period_end)}
                </TD>
                <TD>
                  <StatusBadge status={run.status} />
                </TD>
                <TD className="tabular-nums">{run.staff_count}</TD>
                <TD className="tabular-nums">{run.total_regular_hours}</TD>
                <TD className="tabular-nums">{run.total_overtime_hours}</TD>
                <TD className="tabular-nums">R {run.total_gross_pay}</TD>
                <TD>{formatDate(run.created_at)}</TD>
                <TD>
                  {run.status === "draft" && (
                    <button
                      className="text-xs text-[var(--status-critical)] hover:underline"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(run);
                      }}
                    >
                      Delete
                    </button>
                  )}
                </TD>
              </TR>
            ))}
            {items.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]">No payroll runs yet.</TD>
              </TR>
            )}
          </tbody>
        </Table>
      )}

      {selected && (
        <div className="mt-6 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="text-sm font-semibold text-[var(--text-primary)]">
                Payroll: {formatDate(selected.period_start)} – {formatDate(selected.period_end)}
              </h3>
              <div className="mt-1"><StatusBadge status={selected.status} /></div>
            </div>
            <div className="flex flex-wrap gap-2">
              <button className={btnSecondary} disabled={busyAction} onClick={() => handleExport(selected)}>
                Export CSV
              </button>
              {selected.status === "draft" && (
                <>
                  <button className={btnSecondary} disabled={busyAction} onClick={() => handleRecalculate(selected)}>
                    Recalculate
                  </button>
                  <button
                    className="rounded-md border border-[var(--status-critical)] px-4 py-2 text-sm font-medium text-[var(--status-critical)] hover:bg-[var(--tint-hover)] disabled:opacity-50"
                    disabled={busyAction}
                    onClick={() => handleDelete(selected)}
                  >
                    Delete
                  </button>
                  <button className={btnPrimary} disabled={busyAction} onClick={() => handleFinalize(selected)}>
                    Finalize
                  </button>
                </>
              )}
              <button className={btnSecondary} onClick={() => setSelected(null)}>
                Close
              </button>
            </div>
          </div>
          <Table>
            <THead>
              <tr>
                <TH>Employee #</TH>
                <TH>Name</TH>
                <TH>Pay type</TH>
                <TH>Regular hours</TH>
                <TH>Overtime hours</TH>
                <TH>Rate</TH>
                <TH>OT rate</TH>
                <TH>Base pay</TH>
                <TH>Overtime pay</TH>
                <TH>Gross pay</TH>
              </tr>
            </THead>
            <tbody>
              {selected.lines.map((line) => (
                <TR key={line.id}>
                  <TD>{line.employee_number}</TD>
                  <TD className="font-medium">{line.staff_name}</TD>
                  <TD>{PAY_TYPE_LABEL[line.pay_type]}</TD>
                  <TD className="tabular-nums">{line.regular_hours}</TD>
                  <TD className="tabular-nums">{line.overtime_hours}</TD>
                  <TD className="tabular-nums">R {line.hourly_rate}</TD>
                  <TD className="tabular-nums">R {line.overtime_rate}</TD>
                  <TD className="tabular-nums">R {line.base_pay}</TD>
                  <TD className="tabular-nums">R {line.overtime_pay}</TD>
                  <TD className="tabular-nums font-medium">R {line.gross_pay}</TD>
                </TR>
              ))}
              {selected.lines.length === 0 && (
                <TR>
                  <TD className="text-[var(--text-muted)]">
                    No active employees with configured pay had matching attendance in this period.
                  </TD>
                </TR>
              )}
            </tbody>
          </Table>
        </div>
      )}

      {showModal && (
        <Modal title="Generate payroll run" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Period start">
              <input
                type="date"
                className={inputClass}
                required
                value={periodStart}
                onChange={(e) => setPeriodStart(e.target.value)}
              />
            </FormField>
            <FormField label="Period end">
              <input
                type="date"
                className={inputClass}
                required
                value={periodEnd}
                onChange={(e) => setPeriodEnd(e.target.value)}
              />
            </FormField>
            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
            <p className="mb-3 text-xs text-[var(--text-muted)]">
              Hours and overtime are calculated from clocked attendance in this range; salaried staff are paid their
              full monthly salary for a full-month period, prorated for a shorter one.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" className={btnPrimary} disabled={saving}>
                {saving ? "Generating…" : "Generate"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
