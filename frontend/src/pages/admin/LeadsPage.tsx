import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { FollowUpCell, LeadStageStrip, LeadStatusBadge, LEAD_SOURCES } from "../../components/LeadBits";
import type {
  Lead, LeadStatus, PipelineSummary, Paginated, Partner, Tariff, User,
} from "../../types";

/**
 * The pipeline, as a filterable list rather than a drag-and-drop board.
 *
 * A board looks better in a screenshot and is worse to work: it can't be
 * sorted, can't be searched, and hides everything below the fold of each
 * column. What a rep actually does each morning is work one list — so the
 * stage strip across the top carries the board's real value (where the
 * deals and the money are sitting) and doubles as the filter.
 */

const EMPTY_FORM = {
  full_name: "",
  company_name: "",
  email: "",
  phone: "",
  address: "",
  city: "",
  source: "phone",
  source_detail: "",
  partner: "",
  assigned_to: "",
  interested_tariff: "",
  estimated_monthly_value: "",
  next_follow_up: "",
  notes: "",
};

function formatMoney(value: string | number) {
  const n = typeof value === "string" ? parseFloat(value) : value;
  if (!Number.isFinite(n)) return "R 0";
  return `R ${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

export function LeadsPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [leads, setLeads] = useState<Lead[]>([]);
  const [count, setCount] = useState(0);
  const [summary, setSummary] = useState<PipelineSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [stage, setStage] = useState<LeadStatus | "">("");
  const [view, setView] = useState<"open" | "due" | "all">("open");
  const [mine, setMine] = useState(false);
  const [search, setSearch] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const { items: partners } = useApiList<Partner>("/partners/?page_size=200&ordering=name");
  const { items: tariffs } = useApiList<Tariff>("/tariffs/?page_size=200");
  const { items: staff } = useApiList<User>("/staff-users/");

  const load = useCallback(() => {
    const params = new URLSearchParams({ page_size: "100" });
    // An explicit stage wins over the view toggle -- clicking "Lost" on
    // the strip has to show lost leads even though the default view is
    // open ones, or the strip looks broken.
    if (stage) params.set("status", stage);
    else if (view === "open") params.set("open", "true");
    if (!stage && view === "due") params.set("due", "true");
    if (mine) params.set("mine", "true");
    if (search) params.set("search", search);

    setLoading(true);
    Promise.all([
      api.get<Paginated<Lead>>(`/leads/?${params.toString()}`),
      api.get<PipelineSummary>(`/pipeline-summary/${mine ? "?mine=true" : ""}`),
    ])
      .then(([list, sum]) => {
        setLeads(list.data.results);
        setCount(list.data.count);
        setSummary(sum.data);
        setError("");
      })
      .catch(() => setError("Couldn't load the pipeline."))
      .finally(() => setLoading(false));
  }, [stage, view, mine, search]);

  useEffect(load, [load]);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      const payload: Record<string, unknown> = { ...form };
      // Empty strings are not empty foreign keys. Sending "" for a
      // nullable FK is a 400 that reads like a validation failure on a
      // field the person never touched.
      for (const key of ["partner", "assigned_to", "interested_tariff", "estimated_monthly_value", "next_follow_up"]) {
        if (!payload[key]) payload[key] = null;
      }
      const res = await api.post<Lead>("/leads/", payload);
      setShowNew(false);
      setForm(EMPTY_FORM);
      navigate(`/admin/leads/${res.data.id}`);
    } catch {
      setError("Couldn't save that lead. A name is the only thing that's required.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Leads"
        subtitle="Enquiries, from first contact to won or lost."
        actions={
          <button className={btnPrimary} onClick={() => setShowNew(true)}>
            + New lead
          </button>
        }
      />

      {summary && (
        <LeadStageStrip
          summary={summary}
          active={stage}
          onPick={(next) => {
            setStage(next);
            if (next) setView("all");
          }}
        />
      )}

      {summary && summary.unscheduled_count > 0 && (
        <p className="mb-4 rounded-md border border-[var(--border-hairline)] bg-[var(--tint-subtle)] px-3 py-2 text-xs text-[var(--text-secondary)]">
          <strong className="text-[var(--text-primary)]">{summary.unscheduled_count}</strong> open{" "}
          {summary.unscheduled_count === 1 ? "lead has" : "leads have"} no follow-up date. Those aren't
          late — they're invisible, which is worse.
        </p>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="flex gap-1 rounded-md bg-[var(--tint-subtle)] p-0.5">
          {([
            ["open", "Open"],
            ["due", `Follow up${summary?.due_count ? ` (${summary.due_count})` : ""}`],
            ["all", "All"],
          ] as [typeof view, string][]).map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => {
                setView(key);
                setStage("");
              }}
              className={`rounded px-3 py-1.5 text-xs font-medium transition-colors ${
                view === key && !stage
                  ? "bg-[var(--surface-1)] text-[var(--text-primary)] shadow-sm"
                  : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        <label className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
          <input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} />
          Only mine
        </label>

        <input
          className={inputClass}
          style={{ maxWidth: 260 }}
          placeholder="Search name, company, phone, area…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />

        <span className="ml-auto text-xs text-[var(--text-muted)]">
          {count} {count === 1 ? "lead" : "leads"}
        </span>
      </div>

      {error && (
        <p className="mb-3 rounded-md bg-[var(--status-critical)]/10 px-3 py-2 text-sm text-[var(--status-critical)]">
          {error}
        </p>
      )}

      {loading && leads.length === 0 ? (
        <p className="py-8 text-sm text-[var(--text-muted)]">Loading…</p>
      ) : leads.length === 0 ? (
        <p className="py-8 text-sm text-[var(--text-muted)]">
          {view === "due"
            ? "Nothing to chase today."
            : search
              ? "No leads match that search."
              : "No leads yet. Add the first one with the button above."}
        </p>
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>Name</TH>
              <TH>Stage</TH>
              <TH>Interested in</TH>
              <TH>Value / month</TH>
              <TH>Source</TH>
              <TH>Owner</TH>
              <TH>Follow up</TH>
            </TR>
          </THead>
          <tbody>
            {leads.map((lead) => (
              <TR key={lead.id} onClick={() => navigate(`/admin/leads/${lead.id}`)}>
                <TD>
                  <span className="font-medium text-[var(--text-primary)]">
                    {lead.company_name || lead.full_name}
                  </span>
                  {lead.company_name && (
                    <span className="ml-1.5 text-xs text-[var(--text-muted)]">{lead.full_name}</span>
                  )}
                  {lead.city && (
                    <span className="block text-xs text-[var(--text-muted)]">{lead.city}</span>
                  )}
                </TD>
                <TD>
                  <LeadStatusBadge lead={lead} />
                </TD>
                <TD>{lead.tariff_name || <span className="text-[var(--text-muted)]">—</span>}</TD>
                <TD className="tabular-nums">{formatMoney(lead.value)}</TD>
                <TD>
                  {lead.source_display}
                  {lead.partner_name && (
                    <span className="block text-xs text-[var(--text-muted)]">{lead.partner_name}</span>
                  )}
                </TD>
                <TD>{lead.assigned_to_name || <span className="text-[var(--text-muted)]">Unassigned</span>}</TD>
                <TD>
                  <FollowUpCell lead={lead} />
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showNew && (
        <Modal title="New lead" onClose={() => setShowNew(false)}>
          <form onSubmit={handleCreate}>
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
              <FormField label="Where did they come from?">
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
              <FormField label="Which campaign / who referred them">
                <input
                  className={inputClass}
                  placeholder="Optional, but it's what people actually ask"
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
                  <option value="">{user ? `Me (${user.username})` : "Me"}</option>
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
              <FormField label="Follow up on" hint="Leave blank and nobody will be reminded.">
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
            {error && <p className="mt-2 text-sm text-[var(--status-critical)]">{error}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowNew(false)}>
                Cancel
              </button>
              <button type="submit" className={btnPrimary} disabled={saving}>
                {saving ? "Saving…" : "Create lead"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
