import { useEffect, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { useAuth } from "../../context/AuthContext";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, SortableTH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, filterSelectClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { CSVImportModal } from "../../components/CSVImportModal";
import { ColumnToggle, type ColumnDef } from "../../components/ColumnToggle";
import { useColumnVisibility } from "../../hooks/useColumnVisibility";
import {
  SECTION_LABELS,
  type Tariff,
  type EmailTemplate,
  type EmailSettingsConfig,
  type Partner,
  type Section,
  type StaffPermissionEntry,
  type PaymentMethod,
  type BillingDefaultsConfig,
  type ReminderSettingsConfig,
  type SuspensionSettingsConfig,
  type RecurringBillingFields,
  type RecurringPaymentPeriod,
  type ProformaTarget,
} from "../../types";

type Tab = "tariffs" | "email-templates" | "permissions" | "email-settings" | "billing";

// Array-based, like Finance's Invoices tab -- Tariffs needs two buttons
// registered at once ("Import CSV" and "+ New tariff"), while Email
// Templates and Permissions need none.
type ActionButton = { label: string; onClick: () => void; variant?: "primary" | "secondary" };
type NewAction = ActionButton[] | null;

export function ConfigsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  // Permissions is visible to Management too -- reseller-partner
  // visibility (allowed_partners) is a Management-level setting, the same
  // trust tier as approving a customer deletion or managing Partners
  // themselves, even though section access (allowed_sections) within that
  // same tab stays Admin-only (see PermissionsTab's isAdmin prop).
  const isManagement = isAdmin || user?.role === "management";
  const [tab, setTab] = useState<Tab>("tariffs");
  const [newAction, setNewAction] = useState<NewAction>(null);

  // Email Settings stays admin-only -- mirror the backend's
  // EmailSettingsView (IsAdmin-gated) regardless of what's rendered here.
  // OVPN Settings moved to Networking (alongside RADIUS Clients, which
  // already consumes it as the "Push to router" default) -- see
  // NetworkingPage.tsx's OvpnSettingsTab.
  const TABS: { key: Tab; label: string }[] = [
    { key: "tariffs", label: "Tariffs" },
    { key: "email-templates", label: "Email Templates" },
    ...(isManagement ? [{ key: "permissions" as Tab, label: "Permissions" }] : []),
    ...(isAdmin ? [{ key: "email-settings" as Tab, label: "Email Settings" }] : []),
    ...(isAdmin ? [{ key: "billing" as Tab, label: "Billing" }] : []),
  ];

  return (
    <div>
      <PageHeader
        title="Configs"
        subtitle="Platform-wide configuration: pricing plans, email wording, and other settings."
        actions={
          newAction && (
            <>
              {newAction.map((a) => (
                <button key={a.label} className={a.variant === "secondary" ? btnSecondary : btnPrimary} onClick={a.onClick}>
                  {a.label}
                </button>
              ))}
            </>
          )
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
      {tab === "tariffs" && <TariffsTab onRegisterNewAction={setNewAction} />}
      {tab === "email-templates" && <EmailTemplatesTab onRegisterNewAction={setNewAction} />}
      {tab === "permissions" && isManagement && <PermissionsTab isAdmin={isAdmin} onRegisterNewAction={setNewAction} />}
      {tab === "email-settings" && isAdmin && <EmailSettingsTab onRegisterNewAction={setNewAction} />}
      {tab === "billing" && isAdmin && <BillingConfigTab onRegisterNewAction={setNewAction} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tariffs
// ---------------------------------------------------------------------------

const TARIFF_COLUMNS: ColumnDef[] = [
  { key: "name", label: "Name" },
  { key: "type", label: "Type" },
  { key: "speed", label: "Speed" },
  { key: "price", label: "Price" },
  { key: "billing_period", label: "Billing period" },
  { key: "status", label: "Status" },
];

const IMPORT_TEMPLATE_HEADERS = [
  "name", "service_type", "price", "billing_period",
  "speed_download_mbps", "speed_upload_mbps", "data_cap_gb",
  "tax_rate_pct", "is_active", "description",
];

const EMPTY_TARIFF: Partial<Tariff> = {
  name: "",
  service_type: "internet",
  price: "",
  billing_period: "monthly",
  speed_download_mbps: null,
  speed_upload_mbps: null,
  tax_rate_pct: "15",
  is_active: true,
};

function TariffsTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const [ordering, setOrdering] = useState("name");
  const [typeFilter, setTypeFilter] = useState("");
  const [activeFilter, setActiveFilter] = useState("");
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("tariffs", ["name"]);
  const { items, loading, refetch } = useApiList<Tariff>(
    `/tariffs/?page_size=100&ordering=${ordering}${typeFilter ? `&service_type=${typeFilter}` : ""}${
      activeFilter ? `&is_active=${activeFilter}` : ""
    }`
  );
  const [showModal, setShowModal] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [form, setForm] = useState<Partial<Tariff>>(EMPTY_TARIFF);
  const [saving, setSaving] = useState(false);

  function toggleSort(field: string) {
    setOrdering((prev) => (prev === field ? `-${field}` : prev === `-${field}` ? "name" : field));
  }

  useEffect(() => {
    onRegisterNewAction([
      { label: "Import CSV", variant: "secondary", onClick: () => setShowImport(true) },
      { label: "+ New tariff", onClick: () => setShowModal(true) },
    ]);
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await api.post("/tariffs/", form);
      setShowModal(false);
      setForm(EMPTY_TARIFF);
      refetch();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select className={filterSelectClass} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          <option value="">All types</option>
          <option value="internet">Internet</option>
          <option value="voice">Voice</option>
          <option value="bundle">Bundle</option>
          <option value="other">Other</option>
        </select>
        <select className={filterSelectClass} value={activeFilter} onChange={(e) => setActiveFilter(e.target.value)}>
          <option value="">Active & inactive</option>
          <option value="true">Active only</option>
          <option value="false">Inactive only</option>
        </select>
        {(typeFilter || activeFilter) && (
          <button
            type="button"
            className={btnSecondary}
            onClick={() => {
              setTypeFilter("");
              setActiveFilter("");
            }}
          >
            Clear filters
          </button>
        )}
        <div className="ml-auto">
          <ColumnToggle columns={TARIFF_COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["name"]} />
        </div>
      </div>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <SortableTH field="name" ordering={ordering} onSort={toggleSort}>Name</SortableTH>
              {isVisible("type") && <SortableTH field="service_type" ordering={ordering} onSort={toggleSort}>Type</SortableTH>}
              {isVisible("speed") && <SortableTH field="speed_download_mbps" ordering={ordering} onSort={toggleSort}>Speed</SortableTH>}
              {isVisible("price") && <SortableTH field="price" ordering={ordering} onSort={toggleSort}>Price</SortableTH>}
              {isVisible("billing_period") && <SortableTH field="billing_period" ordering={ordering} onSort={toggleSort}>Billing period</SortableTH>}
              {isVisible("status") && <SortableTH field="is_active" ordering={ordering} onSort={toggleSort}>Status</SortableTH>}
            </tr>
          </THead>
          <tbody>
            {items.map((t) => (
              <TR key={t.id}>
                <TD className="font-medium">{t.name}</TD>
                {isVisible("type") && <TD className="capitalize">{t.service_type}</TD>}
                {isVisible("speed") && <TD>{t.speed_download_mbps ? `${t.speed_download_mbps}/${t.speed_upload_mbps} Mbps` : "—"}</TD>}
                {isVisible("price") && <TD className="tabular-nums">R {parseFloat(t.price).toFixed(2)}</TD>}
                {isVisible("billing_period") && <TD className="capitalize">{t.billing_period}</TD>}
                {isVisible("status") && <TD><StatusBadge status={t.is_active ? "active" : "inactive"} /></TD>}
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title="New tariff" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Name">
              <input className={inputClass} required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </FormField>
            <FormField label="Service type">
              <select
                className={inputClass}
                value={form.service_type}
                onChange={(e) => setForm({ ...form, service_type: e.target.value as Tariff["service_type"] })}
              >
                <option value="internet">Internet</option>
                <option value="voice">Voice</option>
                <option value="bundle">Bundle</option>
                <option value="other">Other</option>
              </select>
            </FormField>
            <FormField label="Price (R)">
              <input
                type="number"
                step="0.01"
                className={inputClass}
                required
                value={form.price}
                onChange={(e) => setForm({ ...form, price: e.target.value })}
              />
            </FormField>
            <FormField label="Download speed (Mbps)">
              <input
                type="number"
                className={inputClass}
                value={form.speed_download_mbps ?? ""}
                onChange={(e) => setForm({ ...form, speed_download_mbps: e.target.value ? Number(e.target.value) : null })}
              />
            </FormField>
            <FormField label="Upload speed (Mbps)">
              <input
                type="number"
                className={inputClass}
                value={form.speed_upload_mbps ?? ""}
                onChange={(e) => setForm({ ...form, speed_upload_mbps: e.target.value ? Number(e.target.value) : null })}
              />
            </FormField>
            <FormField label="Billing period">
              <select
                className={inputClass}
                value={form.billing_period}
                onChange={(e) => setForm({ ...form, billing_period: e.target.value as Tariff["billing_period"] })}
              >
                <option value="monthly">Monthly</option>
                <option value="quarterly">Quarterly</option>
                <option value="annually">Annually</option>
              </select>
            </FormField>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : "Create tariff"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {showImport && (
        <CSVImportModal
          title="Import tariffs"
          importUrlBase="/tariffs/"
          templateHeaders={IMPORT_TEMPLATE_HEADERS}
          templateFilename="tariffs_template.csv"
          onClose={() => setShowImport(false)}
          onImported={refetch}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Email templates
// ---------------------------------------------------------------------------

// Kept in sync with notifications/services.py's build_context() on the
// backend -- shown to staff editing a template so they know which
// {{ placeholders }} are available.
const COMMON_PLACEHOLDERS = [
  "company_name", "customer_name", "customer_id", "customer_email", "portal_url", "balance", "today",
];
const EXTRA_PLACEHOLDERS: Record<string, string[]> = {
  statement: ["statement_date"],
  invoice: ["invoice_number", "invoice_total", "invoice_due_date"],
  quote: ["invoice_number", "invoice_total", "invoice_due_date (the quote's valid-until date)"],
  proforma: ["invoice_number", "invoice_total", "invoice_due_date"],
  payment_reminder: ["invoice_number (blank if none)", "invoice_due_date (blank if none)"],
  payment_received: ["payment_amount", "invoice_number (blank if this payment wasn't tied to a specific invoice)"],
};

function EmailTemplatesTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const { items, loading, refetch } = useApiList<EmailTemplate>("/email-templates/?page_size=25");
  const [editing, setEditing] = useState<EmailTemplate | null>(null);
  const [form, setForm] = useState({ subject: "", body_html: "" });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    // No create button -- templates are seeded for each fixed email kind
    // (welcome, invoice, statement, etc.) and only edited, never added.
    onRegisterNewAction(null);
  }, [onRegisterNewAction]);

  function openEdit(t: EmailTemplate) {
    setEditing(t);
    setForm({ subject: t.subject, body_html: t.body_html });
    setError("");
  }

  async function handleSave(e: FormEvent) {
    e.preventDefault();
    if (!editing) return;
    setSaving(true);
    setError("");
    try {
      await api.patch(`/email-templates/${editing.id}/`, form);
      setEditing(null);
      refetch();
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setError(typeof firstError === "string" ? firstError : "Could not save this template — please try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <p className="mb-3 text-sm text-[var(--text-secondary)]">
        Customize the wording sent for each type of customer email. Use {"{{ placeholder }}"} syntax to insert customer/invoice details.
      </p>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Template</TH>
              <TH>Subject</TH>
              <TH>Attaches PDF?</TH>
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((t) => (
              <TR key={t.id}>
                <TD className="font-medium">{t.name}</TD>
                <TD className="max-w-md truncate text-[var(--text-secondary)]">{t.subject}</TD>
                <TD>{t.has_attachment ? "Yes" : "No"}</TD>
                <TD>
                  <button type="button" className={btnSecondary} onClick={() => openEdit(t)}>
                    Edit
                  </button>
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {editing && (
        <Modal title={`Edit "${editing.name}" template`} onClose={() => setEditing(null)}>
          <form onSubmit={handleSave}>
            <FormField label="Subject">
              <input
                className={inputClass}
                required
                value={form.subject}
                onChange={(e) => setForm({ ...form, subject: e.target.value })}
              />
            </FormField>
            <FormField label="Body (HTML)">
              <textarea
                className={`${inputClass} font-mono text-xs`}
                rows={10}
                required
                value={form.body_html}
                onChange={(e) => setForm({ ...form, body_html: e.target.value })}
              />
            </FormField>

            <div className="mb-4 rounded-md border border-[var(--border-hairline)] bg-[var(--tint-subtle)] p-3 text-xs text-[var(--text-muted)]">
              <p className="mb-1 font-medium text-[var(--text-secondary)]">Available placeholders</p>
              <p>
                {[...COMMON_PLACEHOLDERS, ...(EXTRA_PLACEHOLDERS[editing.key] ?? [])]
                  .map((p) => `{{ ${p} }}`)
                  .join("  ")}
              </p>
            </div>

            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setEditing(null)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : "Save template"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Permissions -- section access is Admin-only; reseller-partner visibility
// is open to Management too (see StaffPermissionsViewSet.perform_update on
// the backend for the actual split).
// ---------------------------------------------------------------------------

// Preserves the declaration order of SECTION_LABELS (types/index.ts), which
// is kept in sync with AdminLayout.tsx's NAV order -- so the checklist here
// lines up with the order sections appear in the sidebar.
const ALL_SECTIONS = Object.keys(SECTION_LABELS) as Section[];

function PermissionsTab({ isAdmin, onRegisterNewAction }: { isAdmin: boolean; onRegisterNewAction: (action: NewAction) => void }) {
  const { items, loading, setItems } = useApiList<StaffPermissionEntry>("/staff-permissions/?page_size=200");
  const { items: partners } = useApiList<Partner>("/partners/?page_size=200&ordering=name");
  const [editing, setEditing] = useState<StaffPermissionEntry | null>(null);
  // Whether the person being edited is limited to a subset of sections at
  // all. Off = allowed_sections saved as [] (unrestricted, the default).
  // On = only the checked sections below are saved. Admin-only to change.
  const [restricted, setRestricted] = useState(false);
  const [checked, setChecked] = useState<Set<Section>>(new Set());
  // Same idea, but for which reseller partners' customers this staff
  // member can see (User.allowed_partners) -- Management can change this
  // too, not just Admin.
  const [partnersRestricted, setPartnersRestricted] = useState(false);
  const [checkedPartners, setCheckedPartners] = useState<Set<number>>(new Set());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    // No create button -- this only edits existing staff accounts.
    onRegisterNewAction(null);
  }, [onRegisterNewAction]);

  function openEdit(entry: StaffPermissionEntry) {
    setEditing(entry);
    setRestricted(entry.allowed_sections.length > 0);
    setChecked(new Set(entry.allowed_sections));
    setPartnersRestricted(entry.allowed_partners.length > 0);
    setCheckedPartners(new Set(entry.allowed_partners));
    setError("");
  }

  function toggleSection(section: Section) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(section)) {
        next.delete(section);
      } else {
        next.add(section);
      }
      return next;
    });
  }

  function togglePartner(id: number) {
    setCheckedPartners((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function accessSummary(entry: StaffPermissionEntry): string {
    if (entry.role === "admin") return "Full access (Admin)";
    if (entry.allowed_sections.length === 0) return "Full access";
    return entry.allowed_sections.map((s) => SECTION_LABELS[s]).join(", ");
  }

  function partnerAccessSummary(entry: StaffPermissionEntry): string {
    if (entry.role === "admin") return "Sees all partners (Admin)";
    if (entry.allowed_partners.length === 0) return "Sees all partners";
    const names = partners.filter((p) => entry.allowed_partners.includes(p.id)).map((p) => p.name);
    return names.join(", ") || `${entry.allowed_partners.length} partner(s)`;
  }

  async function handleSave(e: FormEvent) {
    e.preventDefault();
    if (!editing) return;
    setSaving(true);
    setError("");
    try {
      // allowed_sections is only ever sent when an Admin is the one
      // saving -- the backend rejects the key outright from anyone else
      // (see StaffPermissionsViewSet.perform_update), so a Management
      // user's save must omit it entirely rather than resend the
      // unchanged value.
      const payload: Record<string, unknown> = {
        allowed_partners: partnersRestricted ? Array.from(checkedPartners) : [],
      };
      if (isAdmin) {
        payload.allowed_sections = restricted ? Array.from(checked) : [];
      }
      const res = await api.patch<StaffPermissionEntry>(`/staff-permissions/${editing.id}/`, payload);
      setItems((prev) => prev.map((it) => (it.id === editing.id ? res.data : it)));
      setEditing(null);
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setError(typeof firstError === "string" ? firstError : "Could not save these permissions — please try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <p className="mb-3 text-sm text-[var(--text-secondary)]">
        Control which sidebar sections each staff member can see and work in, and which reseller partners' customers
        they can see. Leave someone with "Full access"/"Sees all partners" to keep them unrestricted (the default) —
        Admin accounts always have full access regardless of either setting.
      </p>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Name</TH>
              <TH>Username</TH>
              <TH>Role</TH>
              <TH>Status</TH>
              <TH>Section access</TH>
              {partners.length > 0 && <TH>Partner access</TH>}
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((entry) => (
              <TR key={entry.id}>
                <TD className="font-medium">{`${entry.first_name} ${entry.last_name}`.trim() || entry.username}</TD>
                <TD className="text-[var(--text-secondary)]">{entry.username}</TD>
                <TD className="capitalize">{entry.role}</TD>
                <TD><StatusBadge status={entry.is_active ? "active" : "inactive"} /></TD>
                <TD className="max-w-md text-[var(--text-secondary)]">{accessSummary(entry)}</TD>
                {partners.length > 0 && (
                  <TD className="max-w-md text-[var(--text-secondary)]">{partnerAccessSummary(entry)}</TD>
                )}
                <TD>
                  {entry.role !== "admin" && (
                    <button type="button" className={btnSecondary} onClick={() => openEdit(entry)}>
                      Edit
                    </button>
                  )}
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {editing && (
        <Modal
          title={`Permissions — ${`${editing.first_name} ${editing.last_name}`.trim() || editing.username}`}
          onClose={() => setEditing(null)}
        >
          <form onSubmit={handleSave}>
            {isAdmin && (
              <>
                <label className="mb-4 flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={restricted}
                    onChange={(e) => setRestricted(e.target.checked)}
                  />
                  <span className="font-medium text-[var(--text-secondary)]">Restrict to selected sections only</span>
                </label>

                <div className={`mb-4 grid grid-cols-1 gap-2 sm:grid-cols-2 ${restricted ? "" : "opacity-40"}`}>
                  {ALL_SECTIONS.map((section) => (
                    <label key={section} className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        disabled={!restricted}
                        checked={checked.has(section)}
                        onChange={() => toggleSection(section)}
                      />
                      <span>{SECTION_LABELS[section]}</span>
                    </label>
                  ))}
                </div>

                {restricted && checked.size === 0 && (
                  <p className="mb-4 text-xs text-[var(--status-warning,#b45309)]">
                    No sections selected — this has the same effect as "Full access" until at least one is checked.
                  </p>
                )}
              </>
            )}

            {partners.length > 0 && (
              <>
                <div className="my-4 border-t border-[var(--border-hairline)]" />
                <label className="mb-4 flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={partnersRestricted}
                    onChange={(e) => setPartnersRestricted(e.target.checked)}
                  />
                  <span className="font-medium text-[var(--text-secondary)]">Restrict to selected partners only</span>
                </label>

                <div className={`mb-4 grid grid-cols-1 gap-2 sm:grid-cols-2 ${partnersRestricted ? "" : "opacity-40"}`}>
                  {partners.map((p) => (
                    <label key={p.id} className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        disabled={!partnersRestricted}
                        checked={checkedPartners.has(p.id)}
                        onChange={() => togglePartner(p.id)}
                      />
                      <span>{p.name}</span>
                    </label>
                  ))}
                </div>

                {partnersRestricted && checkedPartners.size === 0 && (
                  <p className="mb-4 text-xs text-[var(--status-warning,#b45309)]">
                    No partners selected — this has the same effect as "Sees all partners" until at least one is checked.
                  </p>
                )}
              </>
            )}

            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setEditing(null)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : "Save permissions"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Email Settings (SMTP) -- admin-only
// ---------------------------------------------------------------------------

// Tri-state select for use_tls/use_ssl -- "" means "leave unset, fall back
// to the server's .env default", distinct from an explicit Yes or No.
type TriState = "" | "true" | "false";

function triStateToValue(v: TriState): boolean | null {
  return v === "" ? null : v === "true";
}

function valueToTriState(v: boolean | null | undefined): TriState {
  return v === true ? "true" : v === false ? "false" : "";
}

const EMPTY_EMAIL_SETTINGS_FORM = {
  smtp_host: "",
  smtp_port: "",
  smtp_username: "",
  smtp_password: "",
  use_tls: "" as TriState,
  use_ssl: "" as TriState,
  default_from_email: "",
  company_name: "",
  site_url: "",
};

function EmailSettingsTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const [settings, setSettings] = useState<EmailSettingsConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState(EMPTY_EMAIL_SETTINGS_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [testRecipient, setTestRecipient] = useState("");
  const [testBusy, setTestBusy] = useState(false);
  const [testResult, setTestResult] = useState("");

  function load() {
    setLoading(true);
    api.get<EmailSettingsConfig>("/email-settings/").then((r) => {
      setSettings(r.data);
      setForm({
        smtp_host: r.data.smtp_host,
        smtp_port: r.data.smtp_port != null ? String(r.data.smtp_port) : "",
        smtp_username: r.data.smtp_username,
        smtp_password: "",
        use_tls: valueToTriState(r.data.use_tls),
        use_ssl: valueToTriState(r.data.use_ssl),
        default_from_email: r.data.default_from_email,
        company_name: r.data.company_name,
        site_url: r.data.site_url,
      });
      setLoading(false);
    });
  }

  useEffect(() => {
    load();
    onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaved(false);
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        smtp_host: form.smtp_host,
        smtp_port: form.smtp_port ? Number(form.smtp_port) : null,
        smtp_username: form.smtp_username,
        use_tls: triStateToValue(form.use_tls),
        use_ssl: triStateToValue(form.use_ssl),
        default_from_email: form.default_from_email,
        company_name: form.company_name,
        site_url: form.site_url,
      };
      // Blank password means "leave whatever's stored alone" -- only sent
      // when the admin actually typed a new one.
      if (form.smtp_password) payload.smtp_password = form.smtp_password;
      const res = await api.patch<EmailSettingsConfig>("/email-settings/", payload);
      setSettings(res.data);
      setForm((f) => ({ ...f, smtp_password: "" }));
      setSaved(true);
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setError(typeof firstError === "string" ? firstError : "Could not save these settings — please try again.");
    } finally {
      setSaving(false);
    }
  }

  async function handleTestEmail() {
    setTestBusy(true);
    setTestResult("");
    try {
      const res = await api.post<{ detail: string }>(
        "/email-settings/test/",
        testRecipient ? { recipient: testRecipient } : {}
      );
      setTestResult(res.data.detail);
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setTestResult(detail || "Could not send the test email.");
    } finally {
      setTestBusy(false);
    }
  }

  if (loading) return <p className="text-[var(--text-muted)]">Loading…</p>;

  return (
    <div>
      <p className="mb-4 max-w-2xl text-sm text-[var(--text-secondary)]">
        Configure the SMTP server used to send customer emails, staff password resets, and other outgoing mail.
        Leave any field blank to fall back to the server's own default — you only need to fill in what you want to
        override.
      </p>

      <form onSubmit={handleSubmit} autoComplete="off" className="max-w-2xl rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
        <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
          <FormField label="SMTP host">
            <input
              className={inputClass}
              placeholder="e.g. smtp.office365.com"
              value={form.smtp_host}
              onChange={(e) => setForm({ ...form, smtp_host: e.target.value })}
            />
          </FormField>
          <FormField label="SMTP port">
            <input
              type="number"
              className={inputClass}
              placeholder="e.g. 587"
              value={form.smtp_port}
              onChange={(e) => setForm({ ...form, smtp_port: e.target.value })}
            />
          </FormField>
          <FormField label="SMTP username">
            <input
              className={inputClass}
              value={form.smtp_username}
              onChange={(e) => setForm({ ...form, smtp_username: e.target.value })}
              autoComplete="off"
              name="smtp-username"
            />
          </FormField>
          <FormField label={settings?.smtp_password_set ? "SMTP password (a password is set — leave blank to keep it)" : "SMTP password"}>
            <input
              type="password"
              className={inputClass}
              value={form.smtp_password}
              onChange={(e) => setForm({ ...form, smtp_password: e.target.value })}
              autoComplete="new-password"
              name="smtp-password"
            />
          </FormField>
          <FormField label="Use TLS">
            <select
              className={inputClass}
              value={form.use_tls}
              onChange={(e) => setForm({ ...form, use_tls: e.target.value as TriState })}
            >
              <option value="">Use server default</option>
              <option value="true">Yes</option>
              <option value="false">No</option>
            </select>
          </FormField>
          <FormField label="Use SSL">
            <select
              className={inputClass}
              value={form.use_ssl}
              onChange={(e) => setForm({ ...form, use_ssl: e.target.value as TriState })}
            >
              <option value="">Use server default</option>
              <option value="true">Yes</option>
              <option value="false">No</option>
            </select>
          </FormField>
        </div>

        <FormField label='"From" address'>
          <input
            className={inputClass}
            placeholder='e.g. "Skybre <no-reply@skybre.co.za>"'
            value={form.default_from_email}
            onChange={(e) => setForm({ ...form, default_from_email: e.target.value })}
          />
        </FormField>
        <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
          <FormField label="Company name">
            <input
              className={inputClass}
              placeholder="Used in email subjects/bodies and PDFs"
              value={form.company_name}
              onChange={(e) => setForm({ ...form, company_name: e.target.value })}
            />
          </FormField>
          <FormField label="Site URL">
            <input
              className={inputClass}
              placeholder="e.g. https://skybre.co.za"
              value={form.site_url}
              onChange={(e) => setForm({ ...form, site_url: e.target.value })}
            />
          </FormField>
        </div>

        {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
        {saved && !error && <p className="mb-3 text-sm text-[#0ca30c]">Settings saved.</p>}

        <div className="flex justify-end">
          <button type="submit" disabled={saving} className={btnPrimary}>
            {saving ? "Saving…" : "Save settings"}
          </button>
        </div>
      </form>

      <div className="mt-6 max-w-2xl rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
        <h2 className="mb-1 text-sm font-semibold text-[var(--text-primary)]">Send a test email</h2>
        <p className="mb-3 text-sm text-[var(--text-secondary)]">
          Verifies the settings above actually work, using whatever is currently saved (save first if you just
          changed something).
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <FormField label="Send to (optional — defaults to your own account email)">
            <input
              className={inputClass}
              placeholder="you@example.com"
              value={testRecipient}
              onChange={(e) => setTestRecipient(e.target.value)}
            />
          </FormField>
          <button type="button" className={btnSecondary} disabled={testBusy} onClick={handleTestEmail}>
            {testBusy ? "Sending…" : "Send test email"}
          </button>
        </div>
        {testResult && <p className="mt-3 text-sm text-[var(--text-secondary)]">{testResult}</p>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Billing -- Payment Methods / Billing Defaults / Reminders (admin-only).
// The shared RecurringBillingFieldsFormBody below is also reused by
// CustomerDetailPage's per-customer Billing config section.
// ---------------------------------------------------------------------------

type BillingSubTab = "payment-methods" | "defaults" | "reminders" | "auto-suspension";

const PAYMENT_PERIOD_OPTIONS: { value: RecurringPaymentPeriod; label: string }[] = [
  { value: "monthly", label: "1 month" },
  { value: "quarterly", label: "3 months" },
  { value: "biannually", label: "6 months" },
  { value: "annually", label: "12 months" },
];

const PROFORMA_TARGET_OPTIONS: { value: ProformaTarget; label: string }[] = [
  { value: "current_month", label: "Current month" },
  { value: "next_month", label: "Next month" },
];

// Editable subset of RecurringBillingFields -- numbers/selects held as
// strings while being edited, converted on save. Shared by Billing
// Defaults (this file) and, later, the per-customer Billing config
// section on CustomerDetailPage.tsx.
export interface RecurringBillingFormState {
  payment_period: RecurringPaymentPeriod;
  payment_method: string;
  billing_day: string;
  use_date_of_customer_creation: boolean;
  payment_due_days: string;
  blocking_period_days: string;
  deactivation_period_days: string;
  minimum_balance: string;
  auto_create_invoices: boolean;
  send_billing_notifications: boolean;
  auto_proforma_enabled: boolean;
  proforma_day: string;
  proforma_payment_period: RecurringPaymentPeriod | "";
  create_proforma_for: ProformaTarget;
  reminder_enabled: boolean;
  reminder_1_day: string;
  reminder_2_day: string;
  reminder_3_day: string;
}

export function recurringBillingFieldsToFormState(f: RecurringBillingFields): RecurringBillingFormState {
  return {
    payment_period: f.payment_period,
    payment_method: f.payment_method != null ? String(f.payment_method) : "",
    billing_day: String(f.billing_day),
    use_date_of_customer_creation: f.use_date_of_customer_creation,
    payment_due_days: f.payment_due_days != null ? String(f.payment_due_days) : "",
    blocking_period_days: f.blocking_period_days != null ? String(f.blocking_period_days) : "",
    deactivation_period_days: f.deactivation_period_days != null ? String(f.deactivation_period_days) : "",
    minimum_balance: f.minimum_balance,
    auto_create_invoices: f.auto_create_invoices,
    send_billing_notifications: f.send_billing_notifications,
    auto_proforma_enabled: f.auto_proforma_enabled,
    proforma_day: f.proforma_day != null ? String(f.proforma_day) : "",
    proforma_payment_period: f.proforma_payment_period,
    create_proforma_for: f.create_proforma_for,
    reminder_enabled: f.reminder_enabled,
    reminder_1_day: f.reminder_1_day != null ? String(f.reminder_1_day) : "",
    reminder_2_day: f.reminder_2_day != null ? String(f.reminder_2_day) : "",
    reminder_3_day: f.reminder_3_day != null ? String(f.reminder_3_day) : "",
  };
}

export function recurringBillingFormStateToPayload(s: RecurringBillingFormState): Record<string, unknown> {
  return {
    payment_period: s.payment_period,
    payment_method: s.payment_method ? Number(s.payment_method) : null,
    billing_day: s.billing_day ? Number(s.billing_day) : 1,
    use_date_of_customer_creation: s.use_date_of_customer_creation,
    payment_due_days: s.payment_due_days ? Number(s.payment_due_days) : null,
    blocking_period_days: s.blocking_period_days ? Number(s.blocking_period_days) : null,
    deactivation_period_days: s.deactivation_period_days ? Number(s.deactivation_period_days) : null,
    minimum_balance: s.minimum_balance || "0",
    auto_create_invoices: s.auto_create_invoices,
    send_billing_notifications: s.send_billing_notifications,
    auto_proforma_enabled: s.auto_proforma_enabled,
    proforma_day: s.proforma_day ? Number(s.proforma_day) : null,
    proforma_payment_period: s.proforma_payment_period,
    create_proforma_for: s.create_proforma_for,
    reminder_enabled: s.reminder_enabled,
    reminder_1_day: s.reminder_1_day ? Number(s.reminder_1_day) : null,
    reminder_2_day: s.reminder_2_day ? Number(s.reminder_2_day) : null,
    reminder_3_day: s.reminder_3_day ? Number(s.reminder_3_day) : null,
  };
}

export const EMPTY_RECURRING_BILLING_FORM: RecurringBillingFormState = {
  payment_period: "monthly",
  payment_method: "",
  billing_day: "1",
  use_date_of_customer_creation: false,
  payment_due_days: "",
  blocking_period_days: "",
  deactivation_period_days: "",
  minimum_balance: "0",
  auto_create_invoices: true,
  send_billing_notifications: true,
  auto_proforma_enabled: false,
  proforma_day: "",
  proforma_payment_period: "",
  create_proforma_for: "current_month",
  reminder_enabled: false,
  reminder_1_day: "",
  reminder_2_day: "",
  reminder_3_day: "",
};

// Renders the ~17 shared recurring-billing fields -- used both by Billing
// Defaults (the org-wide template, below) and, in task #112, a customer's
// own Billing config section.
export function RecurringBillingFieldsFormBody({
  form,
  onChange,
  paymentMethods,
}: {
  form: RecurringBillingFormState;
  onChange: (next: RecurringBillingFormState) => void;
  paymentMethods: PaymentMethod[];
}) {
  function set<K extends keyof RecurringBillingFormState>(key: K, value: RecurringBillingFormState[K]) {
    onChange({ ...form, [key]: value });
  }

  return (
    <>
      <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
        <FormField label="Payment period">
          <select
            className={inputClass}
            value={form.payment_period}
            onChange={(e) => set("payment_period", e.target.value as RecurringPaymentPeriod)}
          >
            {PAYMENT_PERIOD_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </FormField>
        <FormField label="Payment method">
          <select className={inputClass} value={form.payment_method} onChange={(e) => set("payment_method", e.target.value)}>
            <option value="">— None —</option>
            {paymentMethods.map((pm) => (
              <option key={pm.id} value={pm.id}>{pm.name}</option>
            ))}
          </select>
        </FormField>
        <FormField label="Billing day (1–28)">
          <input
            type="number"
            min={1}
            max={28}
            className={inputClass}
            value={form.billing_day}
            disabled={form.use_date_of_customer_creation}
            onChange={(e) => set("billing_day", e.target.value)}
          />
        </FormField>
        <FormField label="Payment due (days after billing)">
          <input
            type="number"
            min={0}
            className={inputClass}
            placeholder="Due same day if blank"
            value={form.payment_due_days}
            onChange={(e) => set("payment_due_days", e.target.value)}
          />
        </FormField>
        <FormField label="Blocking period (days overdue before auto-suspend)">
          <input
            type="number"
            min={0}
            className={inputClass}
            placeholder="Never auto-suspend if blank"
            value={form.blocking_period_days}
            onChange={(e) => set("blocking_period_days", e.target.value)}
          />
        </FormField>
        <FormField label="Deactivation period (days after blocking — reserved, not yet active)">
          <input
            type="number"
            min={0}
            className={inputClass}
            value={form.deactivation_period_days}
            onChange={(e) => set("deactivation_period_days", e.target.value)}
          />
        </FormField>
        <FormField label="Minimum balance (credit cushion before blocking counts)">
          <input
            type="number"
            step="0.01"
            className={inputClass}
            value={form.minimum_balance}
            onChange={(e) => set("minimum_balance", e.target.value)}
          />
        </FormField>
      </div>

      <label className="mb-3 flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={form.use_date_of_customer_creation}
          onChange={(e) => set("use_date_of_customer_creation", e.target.checked)}
        />
        <span>Bill on the anniversary of the customer's creation date instead of a fixed billing day</span>
      </label>
      <label className="mb-3 flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={form.auto_create_invoices}
          onChange={(e) => set("auto_create_invoices", e.target.checked)}
        />
        <span>Automatically create invoices each cycle</span>
      </label>
      <label className="mb-3 flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={form.send_billing_notifications}
          onChange={(e) => set("send_billing_notifications", e.target.checked)}
        />
        <span>Send billing/reminder/suspension emails automatically</span>
      </label>

      <div className="my-4 border-t border-[var(--border-hairline)]" />
      <label className="mb-3 flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={form.auto_proforma_enabled}
          onChange={(e) => set("auto_proforma_enabled", e.target.checked)}
        />
        <span className="font-medium text-[var(--text-secondary)]">
          Generate a pro forma invoice instead of a real invoice each cycle
        </span>
      </label>
      <div className={`grid grid-cols-1 gap-x-4 sm:grid-cols-2 ${form.auto_proforma_enabled ? "" : "opacity-40"}`}>
        <FormField label="Pro forma day (1–28)">
          <input
            type="number"
            min={1}
            max={28}
            className={inputClass}
            disabled={!form.auto_proforma_enabled}
            value={form.proforma_day}
            onChange={(e) => set("proforma_day", e.target.value)}
          />
        </FormField>
        <FormField label="Pro forma payment period">
          <select
            className={inputClass}
            disabled={!form.auto_proforma_enabled}
            value={form.proforma_payment_period}
            onChange={(e) => set("proforma_payment_period", e.target.value as RecurringPaymentPeriod)}
          >
            <option value="">Same as payment period</option>
            {PAYMENT_PERIOD_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </FormField>
        <FormField label="Create pro forma for">
          <select
            className={inputClass}
            disabled={!form.auto_proforma_enabled}
            value={form.create_proforma_for}
            onChange={(e) => set("create_proforma_for", e.target.value as ProformaTarget)}
          >
            {PROFORMA_TARGET_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </FormField>
      </div>

      <div className="my-4 border-t border-[var(--border-hairline)]" />
      <label className="mb-3 flex items-center gap-2 text-sm">
        <input type="checkbox" checked={form.reminder_enabled} onChange={(e) => set("reminder_enabled", e.target.checked)} />
        <span className="font-medium text-[var(--text-secondary)]">Send payment reminders for this billing cycle</span>
      </label>
      <p className="mb-2 text-xs text-[var(--text-muted)]">
        Days before the due date each reminder fires — only takes effect if that reminder slot is also enabled under
        Configs → Billing → Reminders.
      </p>
      <div className={`grid grid-cols-1 gap-x-4 sm:grid-cols-3 ${form.reminder_enabled ? "" : "opacity-40"}`}>
        <FormField label="Reminder #1 (days before due)">
          <input
            type="number"
            min={0}
            className={inputClass}
            disabled={!form.reminder_enabled}
            value={form.reminder_1_day}
            onChange={(e) => set("reminder_1_day", e.target.value)}
          />
        </FormField>
        <FormField label="Reminder #2 (days before due)">
          <input
            type="number"
            min={0}
            className={inputClass}
            disabled={!form.reminder_enabled}
            value={form.reminder_2_day}
            onChange={(e) => set("reminder_2_day", e.target.value)}
          />
        </FormField>
        <FormField label="Reminder #3 (days before due)">
          <input
            type="number"
            min={0}
            className={inputClass}
            disabled={!form.reminder_enabled}
            value={form.reminder_3_day}
            onChange={(e) => set("reminder_3_day", e.target.value)}
          />
        </FormField>
      </div>
    </>
  );
}

function BillingConfigTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const [subTab, setSubTab] = useState<BillingSubTab>("payment-methods");

  const SUB_TABS: { key: BillingSubTab; label: string }[] = [
    { key: "payment-methods", label: "Payment Methods" },
    { key: "defaults", label: "Billing Defaults" },
    { key: "reminders", label: "Reminders" },
    { key: "auto-suspension", label: "Auto-suspension" },
  ];

  return (
    <div>
      <div className="mb-4 flex gap-1 border-b border-[var(--border-hairline)]">
        {SUB_TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setSubTab(t.key)}
            className={`px-3 py-1.5 text-sm font-medium ${
              subTab === t.key
                ? "border-b-2 border-[var(--series-1)] text-[var(--text-primary)]"
                : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {subTab === "payment-methods" && <PaymentMethodsSubTab onRegisterNewAction={onRegisterNewAction} />}
      {subTab === "defaults" && <BillingDefaultsSubTab onRegisterNewAction={onRegisterNewAction} />}
      {subTab === "reminders" && <RemindersSubTab onRegisterNewAction={onRegisterNewAction} />}
      {subTab === "auto-suspension" && <AutoSuspensionSubTab onRegisterNewAction={onRegisterNewAction} />}
    </div>
  );
}

// --- Payment Methods --------------------------------------------------------

const EMPTY_PAYMENT_METHOD: Partial<PaymentMethod> = { name: "", is_active: true };

function PaymentMethodsSubTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const { items, loading, refetch } = useApiList<PaymentMethod>("/payment-methods/?page_size=100&ordering=name");
  const [editing, setEditing] = useState<PaymentMethod | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState<Partial<PaymentMethod>>(EMPTY_PAYMENT_METHOD);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    onRegisterNewAction([
      {
        label: "+ New payment method",
        onClick: () => {
          setEditing(null);
          setForm(EMPTY_PAYMENT_METHOD);
          setError("");
          setShowModal(true);
        },
      },
    ]);
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function openEdit(pm: PaymentMethod) {
    setEditing(pm);
    setForm({ name: pm.name, is_active: pm.is_active });
    setError("");
    setShowModal(true);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      if (editing) {
        await api.patch(`/payment-methods/${editing.id}/`, form);
      } else {
        await api.post("/payment-methods/", form);
      }
      setShowModal(false);
      refetch();
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setError(typeof firstError === "string" ? firstError : "Could not save this payment method — please try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <p className="mb-3 text-sm text-[var(--text-secondary)]">
        Named payment conventions (e.g. "EFT 1st", "Cash") tagged onto a customer's billing config for reference —
        these don't drive due-date/blocking timing on their own; that comes from the Billing Defaults / each
        customer's own settings below.
      </p>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Name</TH>
              <TH>Status</TH>
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((pm) => (
              <TR key={pm.id}>
                <TD className="font-medium">{pm.name}</TD>
                <TD><StatusBadge status={pm.is_active ? "active" : "inactive"} /></TD>
                <TD>
                  <button type="button" className={btnSecondary} onClick={() => openEdit(pm)}>
                    Edit
                  </button>
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title={editing ? "Edit payment method" : "New payment method"} onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Name">
              <input
                className={inputClass}
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </FormField>
            <label className="mb-4 flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.is_active ?? true}
                onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
              />
              <span>Active</span>
            </label>

            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : editing ? "Save changes" : "Create payment method"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// --- Billing Defaults --------------------------------------------------------

function BillingDefaultsSubTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const { items: paymentMethods } = useApiList<PaymentMethod>("/payment-methods/?page_size=200&ordering=name");
  const [form, setForm] = useState<RecurringBillingFormState>(EMPTY_RECURRING_BILLING_FORM);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applyResult, setApplyResult] = useState("");
  const [confirmingApply, setConfirmingApply] = useState(false);

  function load() {
    setLoading(true);
    api.get<BillingDefaultsConfig>("/billing-defaults/").then((r) => {
      setForm(recurringBillingFieldsToFormState(r.data));
      setLoading(false);
    });
  }

  useEffect(() => {
    load();
    onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaved(false);
    setSaving(true);
    try {
      const res = await api.patch<BillingDefaultsConfig>("/billing-defaults/", recurringBillingFormStateToPayload(form));
      setForm(recurringBillingFieldsToFormState(res.data));
      setSaved(true);
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setError(typeof firstError === "string" ? firstError : "Could not save these defaults — please try again.");
    } finally {
      setSaving(false);
    }
  }

  async function handleApply() {
    setApplying(true);
    setApplyResult("");
    try {
      const res = await api.post<{ updated: number }>("/billing-defaults/apply-to-existing/");
      setApplyResult(`Applied to ${res.data.updated} existing customer(s) with a billing config. Whether billing is enabled for each was left untouched.`);
    } catch {
      setApplyResult("Could not apply these defaults — please try again.");
    } finally {
      setApplying(false);
      setConfirmingApply(false);
    }
  }

  if (loading) return <p className="text-[var(--text-muted)]">Loading…</p>;

  return (
    <div>
      <p className="mb-4 max-w-2xl text-sm text-[var(--text-secondary)]">
        The template a customer's own Billing config (on their detail page) is seeded from the first time it's
        opened. Changing this never switches recurring billing on for anyone — that's always a specific choice made
        on each customer's own config.
      </p>

      <form onSubmit={handleSubmit} className="max-w-2xl rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
        <RecurringBillingFieldsFormBody form={form} onChange={setForm} paymentMethods={paymentMethods} />

        {error && <p className="mb-3 mt-4 text-sm text-[var(--status-critical)]">{error}</p>}
        {saved && !error && <p className="mb-3 mt-4 text-sm text-[#0ca30c]">Defaults saved.</p>}

        <div className="mt-4 flex justify-end">
          <button type="submit" disabled={saving} className={btnPrimary}>
            {saving ? "Saving…" : "Save defaults"}
          </button>
        </div>
      </form>

      <div className="mt-6 max-w-2xl rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
        <h2 className="mb-1 text-sm font-semibold text-[var(--text-primary)]">Apply to existing customers</h2>
        <p className="mb-3 text-sm text-[var(--text-secondary)]">
          Copies these defaults onto every customer who already has a Billing config, without changing whether
          billing is enabled for them. New customers are seeded automatically the first time their config is opened.
        </p>
        {!confirmingApply ? (
          <button type="button" className={btnSecondary} onClick={() => setConfirmingApply(true)}>
            Apply to existing customers…
          </button>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm text-[var(--text-secondary)]">
              This overwrites the shared fields on every existing customer's Billing config. Continue?
            </span>
            <button type="button" className={btnSecondary} onClick={() => setConfirmingApply(false)}>
              Cancel
            </button>
            <button type="button" disabled={applying} className={btnPrimary} onClick={handleApply}>
              {applying ? "Applying…" : "Yes, apply now"}
            </button>
          </div>
        )}
        {applyResult && <p className="mt-3 text-sm text-[var(--text-secondary)]">{applyResult}</p>}
      </div>
    </div>
  );
}

// --- Reminders (global) ------------------------------------------------------

function RemindersSubTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const [settings, setSettings] = useState<ReminderSettingsConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  function load() {
    setLoading(true);
    api.get<ReminderSettingsConfig>("/reminder-settings/").then((r) => {
      setSettings(r.data);
      setLoading(false);
    });
  }

  useEffect(() => {
    load();
    onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!settings) return;
    setError("");
    setSaved(false);
    setSaving(true);
    try {
      const res = await api.patch<ReminderSettingsConfig>("/reminder-settings/", {
        static_days: settings.static_days,
        reminder_1_enabled: settings.reminder_1_enabled,
        reminder_2_enabled: settings.reminder_2_enabled,
        reminder_3_enabled: settings.reminder_3_enabled,
      });
      setSettings(res.data);
      setSaved(true);
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setError(typeof firstError === "string" ? firstError : "Could not save these settings — please try again.");
    } finally {
      setSaving(false);
    }
  }

  if (loading || !settings) return <p className="text-[var(--text-muted)]">Loading…</p>;

  return (
    <div>
      <p className="mb-4 max-w-2xl text-sm text-[var(--text-secondary)]">
        Global kill-switches for payment reminders. Each customer's own reminder day-offsets (set on their Billing
        config, or via Billing Defaults above) only actually fire if the matching slot here is also switched on —
        individual customers can't override this.
      </p>

      <form onSubmit={handleSubmit} className="max-w-2xl rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
        <label className="mb-4 flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={settings.static_days}
            onChange={(e) => setSettings({ ...settings, static_days: e.target.checked })}
          />
          <span>
            Use static days (a fixed day-offset per reminder). Dynamic day calculation isn't implemented yet, so this
            stays checked.
          </span>
        </label>

        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={settings.reminder_1_enabled}
              onChange={(e) => setSettings({ ...settings, reminder_1_enabled: e.target.checked })}
            />
            <span>Reminder #1 enabled</span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={settings.reminder_2_enabled}
              onChange={(e) => setSettings({ ...settings, reminder_2_enabled: e.target.checked })}
            />
            <span>Reminder #2 enabled</span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={settings.reminder_3_enabled}
              onChange={(e) => setSettings({ ...settings, reminder_3_enabled: e.target.checked })}
            />
            <span>Reminder #3 enabled</span>
          </label>
        </div>

        {error && <p className="mb-3 mt-4 text-sm text-[var(--status-critical)]">{error}</p>}
        {saved && !error && <p className="mb-3 mt-4 text-sm text-[#0ca30c]">Settings saved.</p>}

        <div className="mt-4 flex justify-end">
          <button type="submit" disabled={saving} className={btnPrimary}>
            {saving ? "Saving…" : "Save settings"}
          </button>
        </div>
      </form>
    </div>
  );
}

// --- Auto-suspension (global master switch) ---------------------------------

function AutoSuspensionSubTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const [settings, setSettings] = useState<SuspensionSettingsConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [confirmingEnable, setConfirmingEnable] = useState(false);

  function load() {
    setLoading(true);
    api.get<SuspensionSettingsConfig>("/suspension-settings/").then((r) => {
      setSettings(r.data);
      setLoading(false);
    });
  }

  useEffect(() => {
    load();
    onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function save(nextEnabled: boolean) {
    if (!settings) return;
    setError("");
    setSaved(false);
    setSaving(true);
    try {
      const res = await api.patch<SuspensionSettingsConfig>("/suspension-settings/", {
        auto_suspend_enabled: nextEnabled,
      });
      setSettings(res.data);
      setSaved(true);
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setError(typeof firstError === "string" ? firstError : "Could not save this setting — please try again.");
    } finally {
      setSaving(false);
      setConfirmingEnable(false);
    }
  }

  function handleToggle(checked: boolean) {
    // Turning it OFF is always safe to do immediately. Turning it ON is the
    // action that can suspend real customers on the very next Run, so that
    // direction gets an explicit confirmation step first.
    if (checked) {
      setConfirmingEnable(true);
    } else {
      save(false);
    }
  }

  if (loading || !settings) return <p className="text-[var(--text-muted)]">Loading…</p>;

  return (
    <div>
      <p className="mb-4 max-w-2xl text-sm text-[var(--text-secondary)]">
        The platform-wide master switch for auto-suspension. When this is off, Finance → Recurring Billing's Run
        will never suspend anyone — even for a customer whose own Billing config has a blocking period set. When
        it's on, suspension still only ever applies to customers who are billing-enabled, overdue past their own
        blocking period, and whose balance is worse than their minimum balance.
      </p>

      <div className="max-w-2xl rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
        <label className="flex items-center gap-3 text-sm">
          <input
            type="checkbox"
            checked={settings.auto_suspend_enabled}
            disabled={saving}
            onChange={(e) => handleToggle(e.target.checked)}
          />
          <span className="font-medium text-[var(--text-secondary)]">
            Auto-suspension is currently{" "}
            <span className={settings.auto_suspend_enabled ? "text-[var(--status-critical)]" : ""}>
              {settings.auto_suspend_enabled ? "ON" : "OFF"}
            </span>{" "}
            platform-wide
          </span>
        </label>

        {confirmingEnable && (
          <div className="mt-4 rounded-md border border-[var(--status-critical)] bg-[var(--surface-2)] p-4 text-sm">
            <p className="mb-3">
              Turning this on means the <strong>next</strong> Finance → Recurring Billing Run can suspend real,
              overdue customers' services automatically — not just count them in a Preview. Are you sure?
            </p>
            <div className="flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setConfirmingEnable(false)}>
                Cancel
              </button>
              <button type="button" className={btnPrimary} disabled={saving} onClick={() => save(true)}>
                {saving ? "Saving…" : "Yes, enable auto-suspension"}
              </button>
            </div>
          </div>
        )}

        {error && <p className="mt-4 text-sm text-[var(--status-critical)]">{error}</p>}
        {saved && !error && !confirmingEnable && <p className="mt-4 text-sm text-[#0ca30c]">Settings saved.</p>}
      </div>
    </div>
  );
}
