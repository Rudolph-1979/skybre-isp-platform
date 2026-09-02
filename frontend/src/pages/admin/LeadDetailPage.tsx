import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import {
  LEAD_NOTE_KINDS, LEAD_SOURCES, LOST_REASONS, LeadStatusBadge,
} from "../../components/LeadBits";
import { LEAD_STAGES } from "../../types";
import type { Lead, LeadNote, LeadStatus, Partner, Tariff, User } from "../../types";

/**
 * One lead: who they are, what stage they're at, and the conversation so
 * far. The three things a rep does here are move the stage, book the next
 * follow-up, and write down what was said — so those are the three things
 * reachable without opening a dialog.
 */

function formatDate(iso: string | null) {
  if (!iso) return "—";
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

function formatWhen(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function money(value: string) {
  const n = parseFloat(value);
  return Number.isFinite(n) ? `R ${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : "R 0";
}

export function LeadDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [lead, setLead] = useState<Lead | null>(null);
  const [notes, setNotes] = useState<LeadNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const [noteKind, setNoteKind] = useState("call");
  const [noteBody, setNoteBody] = useState("");
  const [noteFollowUp, setNoteFollowUp] = useState("");

  const [showEdit, setShowEdit] = useState(false);
  const [showLost, setShowLost] = useState(false);
  const [lostReason, setLostReason] = useState("no_coverage");

  const { items: partners } = useApiList<Partner>("/partners/?page_size=200&ordering=name");
  const { items: tariffs } = useApiList<Tariff>("/tariffs/?page_size=200");
  const { items: staff } = useApiList<User>("/staff-users/");

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      api.get<Lead>(`/leads/${id}/`),
      api.get<LeadNote[]>(`/leads/${id}/notes/`),
    ])
      .then(([l, n]) => {
        setLead(l.data);
        setNotes(n.data);
        setError("");
      })
      .catch(() => setError("Couldn't load that lead."))
      .finally(() => setLoading(false));
  }, [id]);

  useEffect(load, [load]);

  async function patch(payload: Record<string, unknown>) {
    setBusy(true);
    setError("");
    try {
      await api.patch(`/leads/${id}/`, payload);
      load();
      return true;
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: Record<string, string[]> } })?.response?.data;
      const first = detail ? Object.values(detail)[0] : null;
      setError(Array.isArray(first) ? first[0] : "Couldn't save that change.");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function moveStage(status: LeadStatus) {
    // Lost needs a reason, so it goes through the dialog rather than
    // straight onto the record. The rule is enforced on the backend too;
    // this just means the person is asked instead of refused.
    if (status === "lost") {
      setShowLost(true);
      return;
    }
    await patch({ status, lost_reason: "" });
  }

  async function addNote(e: FormEvent) {
    e.preventDefault();
    if (!noteBody.trim()) return;
    setBusy(true);
    try {
      await api.post(`/leads/${id}/notes/`, {
        kind: noteKind,
        body: noteBody,
        ...(noteFollowUp ? { next_follow_up: noteFollowUp } : {}),
      });
      setNoteBody("");
      setNoteFollowUp("");
      load();
    } catch {
      setError("Couldn't save that note.");
    } finally {
      setBusy(false);
    }
  }

  async function convert() {
    setBusy(true);
    try {
      const res = await api.post<{ customer_id: number }>(`/leads/${id}/convert/`);
      navigate(`/admin/customers/${res.data.customer_id}`);
    } catch {
      setError("Couldn't convert this lead.");
      setBusy(false);
    }
  }

  if (loading && !lead) return <p className="text-sm text-[var(--text-muted)]">Loading…</p>;
  if (!lead) return <p className="text-sm text-[var(--status-critical)]">{error || "Not found."}</p>;

  const canConvert = !lead.customer;

  return (
    <div>
      <PageHeader
        title={lead.company_name || lead.full_name}
        subtitle={
          lead.company_name ? `${lead.full_name}${lead.city ? ` · ${lead.city}` : ""}` : lead.city || undefined
        }
        actions={
          <>
            <Link to="/admin/leads" className={btnSecondary}>
              Back to leads
            </Link>
            <button className={btnSecondary} onClick={() => setShowEdit(true)}>
              Edit
            </button>
            {canConvert ? (
              <button className={btnPrimary} disabled={busy} onClick={convert}>
                Convert to customer
              </button>
            ) : (
              <Link to={`/admin/customers/${lead.customer}`} className={btnPrimary}>
                Open {lead.customer_reference || "customer"}
              </Link>
            )}
          </>
        }
      />

      {error && (
        <p className="mb-4 rounded-md bg-[var(--status-critical)]/10 px-3 py-2 text-sm text-[var(--status-critical)]">
          {error}
        </p>
      )}

      {/* Stage picker. The whole path is visible rather than a dropdown --
          a rep should be able to see where a deal is without opening
          anything. */}
      <div className="mb-6 flex flex-wrap gap-1.5">
        {LEAD_STAGES.map((s) => (
          <button
            key={s}
            type="button"
            disabled={busy || s === lead.status}
            onClick={() => moveStage(s)}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              s === lead.status
                ? "bg-[var(--series-1)] text-white"
                : "bg-[var(--surface-1)] text-[var(--text-secondary)] ring-1 ring-[var(--border-hairline)] hover:bg-[var(--tint-hover)]"
            }`}
          >
            {s.charAt(0).toUpperCase() + s.slice(1)}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Facts */}
        <div className="space-y-4 lg:col-span-1">
          <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 text-sm">
            <Row label="Stage" value={<LeadStatusBadge lead={lead} />} />
            <Row label="Value / month" value={money(lead.value)} />
            <Row label="Interested in" value={lead.tariff_name || "Not sure yet"} />
            <Row
              label="Follow up"
              value={
                <span className={lead.follow_up_is_due ? "font-medium text-[var(--status-serious)]" : ""}>
                  {formatDate(lead.next_follow_up)}
                  {lead.follow_up_is_due && " · due"}
                </span>
              }
            />
            <Row label="Owner" value={lead.assigned_to_name || "Unassigned"} />
            <Row
              label="Source"
              value={
                <>
                  {lead.source_display}
                  {lead.source_detail && (
                    <span className="block text-xs text-[var(--text-muted)]">{lead.source_detail}</span>
                  )}
                </>
              }
            />
            <Row label="Reseller" value={lead.partner_name || "Direct"} />
          </div>

          <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 text-sm">
            <Row label="Phone" value={lead.phone || "—"} />
            <Row label="Email" value={lead.email || "—"} />
            <Row label="Address" value={[lead.address, lead.city, lead.zip_code].filter(Boolean).join(", ") || "—"} />
            <Row label="Added" value={formatWhen(lead.created_at)} />
            {lead.closed_at && <Row label="Closed" value={formatWhen(lead.closed_at)} />}
          </div>

          {lead.notes && (
            <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                Notes
              </p>
              <p className="whitespace-pre-wrap text-sm text-[var(--text-secondary)]">{lead.notes}</p>
            </div>
          )}
        </div>

        {/* Timeline */}
        <div className="lg:col-span-2">
          <form
            onSubmit={addNote}
            className="mb-4 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4"
          >
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <select
                className={inputClass}
                style={{ maxWidth: 130 }}
                value={noteKind}
                onChange={(e) => setNoteKind(e.target.value)}
              >
                {LEAD_NOTE_KINDS.map((k) => (
                  <option key={k.key} value={k.key}>
                    {k.label}
                  </option>
                ))}
              </select>
              <label className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
                Next follow-up
                <input
                  type="date"
                  className={inputClass}
                  style={{ maxWidth: 160 }}
                  value={noteFollowUp}
                  onChange={(e) => setNoteFollowUp(e.target.value)}
                />
              </label>
            </div>
            <textarea
              className={inputClass}
              rows={2}
              placeholder="What was said?"
              value={noteBody}
              onChange={(e) => setNoteBody(e.target.value)}
            />
            <div className="mt-2 flex items-center justify-between">
              {/* Setting the date here rather than in a second save,
                  because a rep who has just agreed a call-back will
                  record it in the same keystroke or not at all. */}
              <span className="text-xs text-[var(--text-muted)]">
                Setting a date here books the next follow-up too.
              </span>
              <button type="submit" className={btnPrimary} disabled={busy || !noteBody.trim()}>
                Log it
              </button>
            </div>
          </form>

          <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)]">
            {notes.length === 0 ? (
              <p className="px-4 py-6 text-sm text-[var(--text-muted)]">Nothing logged yet.</p>
            ) : (
              <ul>
                {notes.map((note) => (
                  <li
                    key={note.id}
                    className="border-b border-[var(--border-hairline)] px-4 py-3 last:border-0"
                  >
                    <div className="flex flex-wrap items-baseline gap-x-2">
                      <span
                        className={`rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${
                          note.kind === "system"
                            ? "bg-[var(--tint-hover)] text-[var(--text-muted)]"
                            : "bg-[var(--series-1)]/12 text-[var(--series-1)]"
                        }`}
                      >
                        {note.kind_display}
                      </span>
                      <span className="text-sm font-medium text-[var(--text-primary)]">
                        {note.author_name || "System"}
                      </span>
                      <span className="ml-auto text-xs tabular-nums text-[var(--text-muted)]">
                        {formatWhen(note.created_at)}
                      </span>
                    </div>
                    <p className="mt-1 whitespace-pre-wrap text-sm text-[var(--text-secondary)]">
                      {note.body}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>

      {showLost && (
        <Modal title="Mark this lead lost" onClose={() => setShowLost(false)}>
          <p className="mb-3 text-sm text-[var(--text-secondary)]">
            Why did it go? This is the part worth knowing later — it's the difference between
            "our pricing is wrong" and "we need a tower there".
          </p>
          <FormField label="Reason" required>
            <select className={inputClass} value={lostReason} onChange={(e) => setLostReason(e.target.value)}>
              {LOST_REASONS.map((r) => (
                <option key={r.key} value={r.key}>
                  {r.label}
                </option>
              ))}
            </select>
          </FormField>
          <div className="mt-4 flex justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={() => setShowLost(false)}>
              Cancel
            </button>
            <button
              type="button"
              className={btnPrimary}
              disabled={busy}
              onClick={async () => {
                const ok = await patch({ status: "lost", lost_reason: lostReason });
                if (ok) setShowLost(false);
              }}
            >
              Mark lost
            </button>
          </div>
        </Modal>
      )}

      {showEdit && (
        <EditLeadModal
          lead={lead}
          partners={partners}
          tariffs={tariffs}
          staff={staff}
          busy={busy}
          onClose={() => setShowEdit(false)}
          onSave={async (payload) => {
            const ok = await patch(payload);
            if (ok) setShowEdit(false);
          }}
        />
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-[var(--border-hairline)] py-2 last:border-0">
      <span className="text-[var(--text-muted)]">{label}</span>
      <span className="text-right text-[var(--text-primary)]">{value}</span>
    </div>
  );
}

function EditLeadModal({
  lead,
  partners,
  tariffs,
  staff,
  busy,
  onClose,
  onSave,
}: {
  lead: Lead;
  partners: Partner[];
  tariffs: Tariff[];
  staff: User[];
  busy: boolean;
  onClose: () => void;
  onSave: (payload: Record<string, unknown>) => void;
}) {
  const [form, setForm] = useState({
    full_name: lead.full_name,
    company_name: lead.company_name,
    email: lead.email,
    phone: lead.phone,
    address: lead.address,
    city: lead.city,
    zip_code: lead.zip_code,
    source: lead.source,
    source_detail: lead.source_detail,
    partner: lead.partner ? String(lead.partner) : "",
    assigned_to: lead.assigned_to ? String(lead.assigned_to) : "",
    interested_tariff: lead.interested_tariff ? String(lead.interested_tariff) : "",
    estimated_monthly_value: lead.estimated_monthly_value ?? "",
    next_follow_up: lead.next_follow_up ?? "",
    notes: lead.notes,
  });

  return (
    <Modal title="Edit lead" onClose={onClose}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const payload: Record<string, unknown> = { ...form };
          for (const key of [
            "partner", "assigned_to", "interested_tariff", "estimated_monthly_value", "next_follow_up",
          ]) {
            if (!payload[key]) payload[key] = null;
          }
          onSave(payload);
        }}
      >
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <FormField label="Name" required>
            <input
              className={inputClass}
              required
              value={form.full_name}
              onChange={(e) => setForm({ ...form, full_name: e.target.value })}
            />
          </FormField>
          <FormField label="Company">
            <input
              className={inputClass}
              value={form.company_name}
              onChange={(e) => setForm({ ...form, company_name: e.target.value })}
            />
          </FormField>
          <FormField label="Phone">
            <input
              className={inputClass}
              value={form.phone}
              onChange={(e) => setForm({ ...form, phone: e.target.value })}
            />
          </FormField>
          <FormField label="Email">
            <input
              type="email"
              className={inputClass}
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
            />
          </FormField>
          <FormField label="Address">
            <input
              className={inputClass}
              value={form.address}
              onChange={(e) => setForm({ ...form, address: e.target.value })}
            />
          </FormField>
          <FormField label="Area / town">
            <input
              className={inputClass}
              value={form.city}
              onChange={(e) => setForm({ ...form, city: e.target.value })}
            />
          </FormField>
          <FormField label="Source">
            <select
              className={inputClass}
              value={form.source}
              onChange={(e) => setForm({ ...form, source: e.target.value })}
            >
              {LEAD_SOURCES.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.label}
                </option>
              ))}
            </select>
          </FormField>
          <FormField label="Campaign / referrer">
            <input
              className={inputClass}
              value={form.source_detail}
              onChange={(e) => setForm({ ...form, source_detail: e.target.value })}
            />
          </FormField>
          <FormField label="Reseller">
            <select
              className={inputClass}
              value={form.partner}
              onChange={(e) => setForm({ ...form, partner: e.target.value })}
            >
              <option value="">Direct — no reseller</option>
              {partners.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </FormField>
          <FormField label="Owner">
            <select
              className={inputClass}
              value={form.assigned_to}
              onChange={(e) => setForm({ ...form, assigned_to: e.target.value })}
            >
              <option value="">Unassigned</option>
              {staff.map((s) => (
                <option key={s.id} value={s.id}>
                  {`${s.first_name} ${s.last_name}`.trim() || s.username}
                </option>
              ))}
            </select>
          </FormField>
          <FormField label="Interested in">
            <select
              className={inputClass}
              value={form.interested_tariff}
              onChange={(e) => setForm({ ...form, interested_tariff: e.target.value })}
            >
              <option value="">Not sure yet</option>
              {tariffs.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} — R {parseFloat(t.price).toFixed(0)}
                </option>
              ))}
            </select>
          </FormField>
          <FormField label="Value per month" hint="Only if it's not the plan price.">
            <input
              type="number"
              step="0.01"
              className={inputClass}
              value={form.estimated_monthly_value}
              onChange={(e) => setForm({ ...form, estimated_monthly_value: e.target.value })}
            />
          </FormField>
          <FormField label="Follow up on">
            <input
              type="date"
              className={inputClass}
              value={form.next_follow_up}
              onChange={(e) => setForm({ ...form, next_follow_up: e.target.value })}
            />
          </FormField>
        </div>
        <FormField label="Notes">
          <textarea
            className={inputClass}
            rows={3}
            value={form.notes}
            onChange={(e) => setForm({ ...form, notes: e.target.value })}
          />
        </FormField>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className={btnSecondary} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className={btnPrimary} disabled={busy}>
            Save
          </button>
        </div>
      </form>
    </Modal>
  );
}
