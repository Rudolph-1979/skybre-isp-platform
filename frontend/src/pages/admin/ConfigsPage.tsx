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
import { canAccessSection } from "../../utils/permissions";
import { ActivityList } from "../../components/ActivityFeed";
import { SpeedWindowsPanel } from "../../components/SpeedWindowsPanel";
import {
  SECTION_LABELS,
  ROLE_LABELS,
  STAFF_ROLES,
  type Tariff,
  type EmailTemplate,
  type EmailSettingsConfig,
  type Partner,
  type Role,
  type Section,
  type StaffAccountEntry,
  type StaffPermissionEntry,
  type PaymentMethod,
  type BillingDefaultsConfig,
  type ReminderSettingsConfig,
  type SuspensionSettingsConfig,
  type RecurringBillingFields,
  type RecurringPaymentPeriod,
  type ProformaTarget,
  type RadiusNasClient,
  type RadiusNasClientPingStatus,
  type OvpnSettingsConfig,
  type AuditEvent,
  type Paginated,
  type User,
} from "../../types";

type Tab =
  | "tariffs"
  | "email-templates"
  | "users"
  | "permissions"
  | "partners"
  | "email-settings"
  | "billing"
  | "radius"
  | "activity"
  | "speed-windows";

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
  //
  // RADIUS moved here from Networking. It is gated on `networking` section
  // access, NOT `configs`, because the endpoints behind it
  // (/radius-nas-clients/, /ovpn-settings/) still require `networking` on
  // the backend. Gating it on configs instead would show the tab to staff
  // whose every request it makes would 403. So the same people see this
  // screen as before -- only its location changed.
  const canSeeRadius = canAccessSection(user, "networking");
  const TABS: { key: Tab; label: string }[] = [
    { key: "tariffs", label: "Tariffs" },
    { key: "email-templates", label: "Email Templates" },
    ...(isAdmin ? [{ key: "users" as Tab, label: "Users" }] : []),
    ...(isManagement ? [{ key: "permissions" as Tab, label: "Permissions" }] : []),
    ...(isManagement ? [{ key: "partners" as Tab, label: "Partners" }] : []),
    ...(isAdmin ? [{ key: "email-settings" as Tab, label: "Email Settings" }] : []),
    ...(isAdmin ? [{ key: "billing" as Tab, label: "Billing" }] : []),
    ...(canSeeRadius ? [{ key: "radius" as Tab, label: "RADIUS" }] : []),
    // Everyone with Configs access, not just Admin. Restricting it
    // further would mean the one screen that says who did what is
    // readable only by the account most able to do anything.
    { key: "activity", label: "Activity log" },
    // Next to Tariffs, because a window changes what every customer on a
    // plan gets -- the same kind of decision as the plan's own speed.
    ...(isAdmin ? [{ key: "speed-windows" as Tab, label: "Speed windows" }] : []),
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
      {tab === "users" && isAdmin && <UsersTab onRegisterNewAction={setNewAction} />}
      {tab === "permissions" && isManagement && <PermissionsTab isAdmin={isAdmin} onRegisterNewAction={setNewAction} />}
      {tab === "partners" && isManagement && <PartnersTab onRegisterNewAction={setNewAction} />}
      {tab === "email-settings" && isAdmin && <EmailSettingsTab onRegisterNewAction={setNewAction} />}
      {tab === "billing" && isAdmin && <BillingConfigTab onRegisterNewAction={setNewAction} />}
      {tab === "radius" && canSeeRadius && <RadiusClientsTab onRegisterNewAction={setNewAction} />}
      {tab === "activity" && <ActivityLogTab onRegisterNewAction={setNewAction} />}
      {tab === "speed-windows" && isAdmin && <SpeedWindowsPanel />}
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

// Speeds are stored in Kbps. Shown alongside the input so a wrong unit is
// obvious as you type -- 4 Mbps is 4096, and "4" would be 4 Kbps.
function mbpsHint(kbps: number | null | undefined) {
  if (!kbps) return "";
  const mbps = kbps / 1024;
  const rounded = Number.isInteger(mbps) ? mbps : Math.round(mbps * 100) / 100;
  return `${rounded} Mbps`;
}

function speedCell(down: number | null, up: number | null) {
  if (!down) return "—";
  return `${down}/${up ?? "?"} Kbps`;
}

const IMPORT_TEMPLATE_HEADERS = [
  "name", "service_type", "price", "billing_period",
  "speed_download_kbps", "speed_upload_kbps", "data_cap_gb",
  "tax_rate_pct", "is_active", "description",
];

const EMPTY_TARIFF: Partial<Tariff> = {
  name: "",
  service_type: "internet",
  price: "",
  billing_period: "monthly",
  speed_download_kbps: null,
  speed_upload_kbps: null,
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
  // The tariff being edited, or null when the modal is creating a new one.
  // One modal serves both so the two forms can't drift apart -- a field added
  // to create but forgotten on edit is how you end up with a plan you can
  // only fix by deleting and re-adding it.
  const [editing, setEditing] = useState<Tariff | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");

  function toggleSort(field: string) {
    setOrdering((prev) => (prev === field ? `-${field}` : prev === `-${field}` ? "name" : field));
  }

  function openCreate() {
    setEditing(null);
    setForm(EMPTY_TARIFF);
    setError("");
    setShowModal(true);
  }

  function openEdit(tariff: Tariff) {
    setEditing(tariff);
    setForm({ ...tariff });
    setError("");
    setShowModal(true);
  }

  function closeModal() {
    setShowModal(false);
    setEditing(null);
    setForm(EMPTY_TARIFF);
    setError("");
  }

  useEffect(() => {
    onRegisterNewAction([
      { label: "Import CSV", variant: "secondary", onClick: () => setShowImport(true) },
      { label: "+ New tariff", onClick: openCreate },
    ]);
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleDelete() {
    if (!editing) return;
    if (!confirm(`Delete the tariff "${editing.name}"? This can't be undone.`)) return;
    setDeleting(true);
    setError("");
    try {
      await api.delete(`/tariffs/${editing.id}/`);
      closeModal();
      refetch();
    } catch (err) {
      // The backend refuses when services are on the plan or have a change
      // booked onto it, and its message names how many and what to do
      // instead -- so it is shown verbatim rather than replaced with
      // something vaguer.
      const data = (err as { response?: { data?: { detail?: string } } })?.response?.data;
      setError(data?.detail || "Could not delete this tariff.");
    } finally {
      setDeleting(false);
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      if (editing) {
        await api.patch(`/tariffs/${editing.id}/`, form);
      } else {
        await api.post("/tariffs/", form);
      }
      closeModal();
      refetch();
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const first = data ? Object.values(data).flat()[0] : null;
      setError(typeof first === "string" ? first : "Could not save this tariff.");
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

      <p className="mb-3 text-sm text-[var(--text-muted)]">
        Click a tariff to edit it — no need to delete and re-add. Speeds are in Kbps (1 Mbps = 1024).
      </p>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <SortableTH field="name" ordering={ordering} onSort={toggleSort}>Name</SortableTH>
              {isVisible("type") && <SortableTH field="service_type" ordering={ordering} onSort={toggleSort}>Type</SortableTH>}
              {isVisible("speed") && <SortableTH field="speed_download_kbps" ordering={ordering} onSort={toggleSort}>Speed</SortableTH>}
              {isVisible("price") && <SortableTH field="price" ordering={ordering} onSort={toggleSort}>Price</SortableTH>}
              {isVisible("billing_period") && <SortableTH field="billing_period" ordering={ordering} onSort={toggleSort}>Billing period</SortableTH>}
              {isVisible("status") && <SortableTH field="is_active" ordering={ordering} onSort={toggleSort}>Status</SortableTH>}
            </tr>
          </THead>
          <tbody>
            {items.map((t) => (
              <TR key={t.id} onClick={() => openEdit(t)}>
                <TD className="font-medium">{t.name}</TD>
                {isVisible("type") && <TD className="capitalize">{t.service_type}</TD>}
                {isVisible("speed") && (
                  <TD>
                    {speedCell(t.speed_download_kbps, t.speed_upload_kbps)}
                    {t.speed_download_kbps ? (
                      <span className="ml-1 text-xs text-[var(--text-muted)]">({mbpsHint(t.speed_download_kbps)})</span>
                    ) : null}
                  </TD>
                )}
                {isVisible("price") && <TD className="tabular-nums">R {parseFloat(t.price).toFixed(2)}</TD>}
                {isVisible("billing_period") && <TD className="capitalize">{t.billing_period}</TD>}
                {isVisible("status") && <TD><StatusBadge status={t.is_active ? "active" : "inactive"} /></TD>}
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title={editing ? `Edit tariff — ${editing.name}` : "New tariff"} onClose={closeModal}>
          <form onSubmit={handleSubmit}>
            {editing && editing.service_count > 0 && (
              <p className="mb-3 rounded-md border border-[var(--status-warning)] bg-[#fff6e5] p-2 text-xs text-[#a5730a]">
                {editing.service_count} service{editing.service_count === 1 ? "" : "s"} on this plan
                {editing.active_service_count !== editing.service_count
                  ? ` (${editing.active_service_count} active)`
                  : ""}
                . Changing the <strong>price</strong> changes what they're billed on the next run; changing the{" "}
                <strong>speed</strong> re-pushes their rate limit to the router. Neither touches invoices already
                issued.
              </p>
            )}
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
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <FormField label="Download speed (Kbps)">
                <input
                  type="number"
                  className={inputClass}
                  placeholder="4096"
                  value={form.speed_download_kbps ?? ""}
                  onChange={(e) => setForm({ ...form, speed_download_kbps: e.target.value ? Number(e.target.value) : null })}
                />
              </FormField>
              <FormField label="Upload speed (Kbps)">
                <input
                  type="number"
                  className={inputClass}
                  placeholder="4096"
                  value={form.speed_upload_kbps ?? ""}
                  onChange={(e) => setForm({ ...form, speed_upload_kbps: e.target.value ? Number(e.target.value) : null })}
                />
              </FormField>
            </div>
            <p className="-mt-1 mb-3 text-xs text-[var(--text-muted)]">
              In <strong>Kbps</strong> — 1 Mbps is 1024, so 4 Mbps is 4096 and 10 Mbps is 10240.
              {form.speed_download_kbps || form.speed_upload_kbps ? (
                <>
                  {" "}That's{" "}
                  <strong>
                    {mbpsHint(form.speed_download_kbps) || "—"} down / {mbpsHint(form.speed_upload_kbps) || "—"} up
                  </strong>
                  .
                </>
              ) : null}
            </p>
            {/* Said out loud because leaving these blank does NOT fail: the
                rate limit falls back to 10240 Kbps, so everyone on the plan
                silently gets 10 Mbps at whatever price this charges. Internet
                only -- a voice plan having no speed is normal. */}
            {form.service_type === "internet" &&
              (!form.speed_download_kbps || !form.speed_upload_kbps) && (
                <p className="mb-3 rounded-md border border-[var(--status-warning)] bg-[#fff6e5] p-2 text-xs text-[#a5730a]">
                  No speed set. Saving like this doesn't fail — it hands everyone on this plan a fallback of{" "}
                  <strong>10240 Kbps (10 Mbps)</strong> regardless of what they pay.
                </p>
              )}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <FormField label="VAT rate (%)">
                <input
                  type="number"
                  step="0.01"
                  className={inputClass}
                  value={form.tax_rate_pct ?? ""}
                  onChange={(e) => setForm({ ...form, tax_rate_pct: e.target.value })}
                />
              </FormField>
              <FormField label="Data cap (GB)">
                <input
                  type="number"
                  className={inputClass}
                  placeholder="Blank = unlimited"
                  value={form.data_cap_gb ?? ""}
                  onChange={(e) => setForm({ ...form, data_cap_gb: e.target.value ? Number(e.target.value) : null })}
                />
              </FormField>
            </div>

            {/* Fair use is NOT the data cap above, and the two are kept
                apart on purpose. A cap is a bundle somebody bought and can
                run out of; fair use is a threshold on an uncapped plan
                past which heavy users are shaped so everybody else keeps
                working. The cap is also what the CUSTOMER sees on their
                own usage page — so shaping through it would show people a
                cap on a plan sold to them as uncapped. */}
            <div className="mt-4 border-t border-[var(--border-hairline)] pt-4">
              <p className="mb-1 text-sm font-semibold text-[var(--text-primary)]">Fair use</p>
              <p className="mb-3 text-xs text-[var(--text-muted)]">
                For uncapped plans. Past the threshold the line is slowed, not cut off — and it
                still gets any speed-window boost. Leave the threshold blank for no fair-use
                shaping at all, which is how every plan behaves today.
              </p>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <FormField label="Threshold (GB / month)" hint="Blank = no shaping, ever.">
                  <input
                    type="number"
                    className={inputClass}
                    placeholder="Blank = no fair-use shaping"
                    value={form.fup_threshold_gb ?? ""}
                    onChange={(e) =>
                      setForm({ ...form, fup_threshold_gb: e.target.value ? Number(e.target.value) : null })
                    }
                  />
                </FormField>
                <FormField
                  label="Shaped speed (% of plan)"
                  // The percentage on its own is not a number anybody can
                  // quote down the phone, so the resolved speed is shown
                  // next to it.
                  hint={
                    form.speed_download_kbps
                      ? `${form.name || "This plan"} would run at ${(
                          (form.speed_download_kbps * (form.fup_speed_pct ?? 30)) / 100 / 1024
                        ).toFixed(1)} Mbps once shaped.`
                      : "30 = 30% of the plan speed."
                  }
                >
                  <input
                    type="number"
                    min={1}
                    max={100}
                    className={inputClass}
                    value={form.fup_speed_pct ?? 30}
                    onChange={(e) => setForm({ ...form, fup_speed_pct: Number(e.target.value) })}
                  />
                </FormField>
              </div>
            </div>
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
            <FormField label="Description (optional)">
              <textarea
                className={inputClass}
                rows={2}
                value={form.description ?? ""}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
            </FormField>
            {/* Retiring a plan without breaking the customers on it: inactive
                hides it from the "new service" dropdowns, and every existing
                service carries on billing at it. */}
            <label className="mb-2 flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={form.is_active ?? true}
                onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
              />
              <span className="text-[var(--text-secondary)]">
                Active — offered when adding a new service. Unticking it doesn't affect services already on this plan.
              </span>
            </label>
            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
            <div className="mt-4 flex items-center justify-between gap-2">
              {/* Far left, deliberately away from Save. Only when editing --
                  there is nothing to delete on a plan that doesn't exist yet. */}
              {editing ? (
                <button
                  type="button"
                  disabled={deleting || saving}
                  className="text-sm text-red-600 hover:underline disabled:opacity-50 dark:text-red-400"
                  onClick={handleDelete}
                >
                  {deleting ? "Deleting…" : "Delete tariff"}
                </button>
              ) : (
                <span />
              )}
              <div className="flex gap-2">
                <button type="button" className={btnSecondary} onClick={closeModal}>
                  Cancel
                </button>
                <button type="submit" disabled={saving} className={btnPrimary}>
                  {saving ? "Saving…" : editing ? "Save changes" : "Create tariff"}
                </button>
              </div>
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
// Users (staff account lifecycle -- create, edit, suspend/reactivate,
// permanently delete). Admin-only. Moved here from the Staff page
// (2026-08-19) so all staff-account administration lives in one place --
// distinct from Permissions just below, which only manages section access
// on an existing account rather than the account itself.
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
  const [inviteResult, setInviteResult] = useState<{ sent: boolean; detail: string } | null>(null);
  const [inviteBusyId, setInviteBusyId] = useState<number | null>(null);
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
    onRegisterNewAction([
      {
        label: "+ Add user",
        onClick: () => {
          setEditing(null);
          setForm(EMPTY_USER_FORM);
          setError("");
          setShowModal(true);
        },
      },
    ]);
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
        const res = await api.post<{ invite?: { sent: boolean; detail: string } }>(
          "/staff-accounts/",
          payload
        );
        // Reported, never assumed. An SMTP outage doesn't undo the account,
        // so without this the admin walks away believing an invite went out
        // and the new person waits for an email that never arrives.
        if (res.data.invite) setInviteResult(res.data.invite);
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

  async function handleSendInvite(entry: StaffAccountEntry) {
    setInviteBusyId(entry.id);
    try {
      const res = await api.post<{ detail: string }>(`/staff-accounts/${entry.id}/send_invite/`);
      alert(res.data.detail);
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      alert(detail || "Could not send the invite.");
    } finally {
      setInviteBusyId(null);
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

      {inviteResult && (
        <p
          className={`mb-3 rounded-md border p-2 text-sm ${
            inviteResult.sent
              ? "border-[var(--status-good)] text-[var(--text-secondary)]"
              : "border-[var(--status-warning)] bg-[#fff6e5] text-[#a5730a]"
          }`}
        >
          {inviteResult.detail}{" "}
          <button className="underline" onClick={() => setInviteResult(null)}>Dismiss</button>
        </p>
      )}

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
                        {/* Separate from "Send reset link" because the two say
                            different things to whoever opens them: an invite
                            announces an account and gives the username; a
                            reset assumes they already know about it. Same
                            token underneath -- the wording is the point. */}
                        <button
                          className="ml-2 text-xs text-[var(--series-1)] hover:underline disabled:opacity-50"
                          disabled={inviteBusyId === entry.id || !entry.email}
                          title={entry.email ? "Email them an invite to set their password" : "Add an email address (via Edit) before sending an invite"}
                          onClick={() => handleSendInvite(entry)}
                        >
                          {inviteBusyId === entry.id ? "Sending…" : "Send invite"}
                        </button>
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
            <FormField
              label={
                editing
                  ? "New password (leave blank to keep current)"
                  : "Password (leave blank to send them an invite)"
              }
            >
              <input
                type="password"
                className={inputClass}
                minLength={8}
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                autoComplete="new-password"
                name="staff-password"
              />
            </FormField>
            {!editing && !form.password && (
              // The better path, so it is the default and it is explained.
              // Setting a password here means the admin invents one and then
              // has to get it to the person somehow -- read out, messaged,
              // written down -- which is a password chosen by the wrong
              // person, sitting somewhere it shouldn't.
              <p className="-mt-2 mb-3 text-xs text-[var(--text-muted)]">
                Leave this blank and they'll be emailed an invite to choose their own password. The
                account can't be signed into until they do.
              </p>
            )}
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
// Partners (reselling) -- Management/Admin. Moved here from the Staff page
// (2026-08-19) so it lives alongside Permissions, which is where
// reseller-partner visibility (allowed_partners) for staff is configured.
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
    onRegisterNewAction([
      {
        label: "+ Add partner",
        onClick: () => {
          setEditing(null);
          setForm(EMPTY_PARTNER_FORM);
          setError("");
          setShowModal(true);
        },
      },
    ]);
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
  const [vatNumber, setVatNumber] = useState("");
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
      setVatNumber(r.data.vat_number ?? "");
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
      const res = await api.patch<BillingDefaultsConfig>("/billing-defaults/", {
        ...recurringBillingFormStateToPayload(form),
        vat_number: vatNumber,
      });
      setForm(recurringBillingFieldsToFormState(res.data));
      setVatNumber(res.data.vat_number ?? "");
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

        <div className="mt-4">
          <FormField label="VAT number">
            <input
              type="text"
              className={inputClass}
              value={vatNumber}
              onChange={(e) => setVatNumber(e.target.value)}
              placeholder="e.g. 4123456789"
            />
          </FormField>
          <p className="mt-1 text-xs text-[var(--text-muted)]">
            Skybre's own SARS VAT registration number — shown on VAT return PDFs under Accountant → VAT Returns.
          </p>
        </div>

        {error && <p className="mb-3 mt-4 text-sm text-[var(--status-critical)]">{error}</p>}
        {saved && !error && <p className="mb-3 mt-4 text-sm text-[#0ca30c]">Defaults saved.</p>}

        <div className="mt-4 flex justify-end">
          <button type="submit" disabled={saving} className={btnPrimary}>
            {saving ? "Saving…" : "Save defaults"}
          </button>
        </div>
      </form>

      <InvoiceCompanyCard />

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


// --- Company details printed on invoices -------------------------------------
//
// Its own card, and its own PATCH, rather than more fields on the billing
// defaults form above: those fields are a template copied onto each
// customer's billing config, these are single company-wide facts printed on
// a document. Mixing them would make "Apply to existing customers" read as
// though it might touch the letterhead.
//
// A tax invoice is not valid without the supplier's registered name, address
// and VAT number, so this is the difference between an invoice a customer's
// accountant will accept and one they won't.

type CompanyFieldKey =
  | "company_legal_name" | "company_address" | "company_city" | "company_postal_code"
  | "company_country" | "company_phone" | "company_email"
  | "bank_name" | "bank_account_number" | "bank_branch_code";

type CompanyField = { key: CompanyFieldKey; label: string; placeholder?: string };

// Laid out as explicit rows: a one-field row is full width, a two-field row
// splits. Written out rather than computed so what you read is what renders.
const COMPANY_ROWS: CompanyField[][] = [
  [{ key: "company_legal_name", label: "Registered company name", placeholder: "e.g. Skybre Pty Ltd" }],
  [{ key: "company_address", label: "Street address", placeholder: "e.g. Cnr Reitz & Botha Street" }],
  [
    { key: "company_city", label: "City" },
    { key: "company_postal_code", label: "Postal code" },
  ],
  [
    { key: "company_country", label: "Country" },
    { key: "company_phone", label: "Phone" },
  ],
  [{ key: "company_email", label: "Billing email", placeholder: "e.g. accounts@skybre.co.za" }],
];

const BANK_ROWS: CompanyField[][] = [
  [
    { key: "bank_name", label: "Bank", placeholder: "e.g. FNB" },
    { key: "bank_branch_code", label: "Branch code", placeholder: "e.g. 250655" },
  ],
  [{ key: "bank_account_number", label: "Account number" }],
];

const ALL_COMPANY_FIELDS: CompanyField[] = [...COMPANY_ROWS, ...BANK_ROWS].flat();

type CompanyForm = Record<string, string>;

function InvoiceCompanyCard() {
  const [form, setForm] = useState<CompanyForm>({});
  const [logoUrl, setLogoUrl] = useState<string | null>(null);
  const [logoName, setLogoName] = useState<string | null>(null);
  const [logoFile, setLogoFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  function absorb(data: BillingDefaultsConfig) {
    const next: CompanyForm = {};
    for (const f of ALL_COMPANY_FIELDS) next[f.key] = (data[f.key] as string) ?? "";
    setForm(next);
    setLogoUrl(data.logo_url ?? null);
    setLogoName(data.logo_name ?? null);
    setLogoFile(null);
  }

  useEffect(() => {
    api.get<BillingDefaultsConfig>("/billing-defaults/").then((r) => {
      absorb(r.data);
      setLoading(false);
    });
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaved(false);
    setSaving(true);
    try {
      // A chosen logo forces multipart -- a file cannot go through JSON.
      // Without one, a plain JSON PATCH keeps the request (and the server's
      // parser path) as simple as every other settings form here.
      let res;
      if (logoFile) {
        const body = new FormData();
        for (const [k, v] of Object.entries(form)) body.append(k, v);
        body.append("logo", logoFile);
        res = await api.patch<BillingDefaultsConfig>("/billing-defaults/", body);
      } else {
        res = await api.patch<BillingDefaultsConfig>("/billing-defaults/", form);
      }
      absorb(res.data);
      setSaved(true);
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setError(typeof firstError === "string" ? firstError : "Could not save these details — please try again.");
    } finally {
      setSaving(false);
    }
  }

  function renderRows(rows: CompanyField[][]) {
    return rows.map((row) => {
      const inputs = row.map((f) => (
        <FormField key={f.key} label={f.label}>
          <input
            type="text"
            className={inputClass}
            value={form[f.key] ?? ""}
            placeholder={f.placeholder}
            onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
          />
        </FormField>
      ));
      if (row.length === 1) return inputs[0];
      return (
        <div key={`row-${row[0].key}`} className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {inputs}
        </div>
      );
    });
  }

  if (loading) return null;

  return (
    <form
      onSubmit={handleSubmit}
      className="mt-6 max-w-2xl rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5"
    >
      <h2 className="mb-1 text-sm font-semibold text-[var(--text-primary)]">Company details on invoices</h2>
      <p className="mb-4 text-sm text-[var(--text-secondary)]">
        Printed on every invoice, quote and pro forma. A tax invoice is only valid if it carries your registered
        name, address and VAT number — the VAT number comes from the field above.
      </p>

      {renderRows(COMPANY_ROWS)}

      <h3 className="mb-2 mt-5 text-sm font-semibold text-[var(--text-primary)]">Payment details</h3>
      <p className="mb-3 text-sm text-[var(--text-secondary)]">
        The one account customers should pay into. Kept separate from the accounts under Finance → Bank feeds —
        those are for importing transactions, and not all of them are where you want money sent.
      </p>
      {renderRows(BANK_ROWS)}

      <div className="mt-4">
        <FormField label="Logo">
          <input
            type="file"
            accept="image/png,image/jpeg,image/gif"
            className={inputClass}
            onChange={(e) => setLogoFile(e.target.files?.[0] ?? null)}
          />
        </FormField>
        <p className="mt-1 text-xs text-[var(--text-muted)]">
          Printed top-left, scaled to 85 × 60 pt. A PNG with a transparent background looks best.
          {logoName && !logoFile ? ` Currently: ${logoName}.` : ""}
          {logoFile ? " Save to replace the current logo." : ""}
        </p>
        {logoUrl && !logoFile && (
          <img
            src={logoUrl}
            alt="Current invoice logo"
            className="mt-3 h-16 w-auto rounded border border-[var(--border-hairline)] bg-white p-1"
          />
        )}
      </div>

      {error && <p className="mb-3 mt-4 text-sm text-[var(--status-critical)]">{error}</p>}
      {saved && !error && <p className="mb-3 mt-4 text-sm text-[#0ca30c]">Company details saved.</p>}

      <div className="mt-4 flex justify-end">
        <button type="submit" disabled={saving} className={btnPrimary}>
          {saving ? "Saving…" : "Save company details"}
        </button>
      </div>
    </form>
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

// ---------------------------------------------------------------------------
// RADIUS -- the Mikrotik/NAS devices allowed to authenticate against this
// platform's FreeRADIUS server. Moved here from Networking; the API is
// still /radius-nas-clients/ and still requires the `networking` section,
// which is why the tab is gated on networking rather than configs access.
// ---------------------------------------------------------------------------

const NAS_COLUMNS: ColumnDef[] = [
  { key: "name", label: "Name" },
  { key: "status", label: "Status" },
  { key: "ip_address", label: "IP address" },
  { key: "shortname", label: "Shortname" },
  { key: "secret", label: "Secret" },
  { key: "realm", label: "Realm" },
  { key: "active", label: "Active" },
];

// How often the RADIUS Clients page re-pings every NAS on its own, in
// addition to the manual "Refresh status" button -- a live network
// check, not a persisted field, so it's always at most this stale.
const NAS_STATUS_REFRESH_MS = 45_000;

const emptyNasForm: Partial<RadiusNasClient> & { secret: string } = {
  name: "",
  ip_address: "",
  shortname: "",
  secret: "",
  realm: "",
  description: "",
  is_active: true,
};

function RadiusClientsTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const { items, loading, refetch } = useApiList<RadiusNasClient>("/radius-nas-clients/?page_size=100");
  const [showModal, setShowModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState<RadiusNasClient | null>(null);
  const [form, setForm] = useState(emptyNasForm);
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("radius-nas-clients", ["name"]);

  useEffect(() => {
    onRegisterNewAction([{ label: "+ New RADIUS client", onClick: () => openCreate() }]);
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Live online/offline status -- a ping run right now against every
  // client's IP, not a persisted field (see RadiusNasClientPingStatus).
  // Keyed by client id so a row shows "Checking…" the first time and
  // then whatever the last completed ping said, rather than flickering
  // back to unknown on every refresh.
  const [statusById, setStatusById] = useState<Record<number, "online" | "offline">>({});
  const [statusChecking, setStatusChecking] = useState(false);

  async function refreshStatuses() {
    setStatusChecking(true);
    try {
      const res = await api.get<RadiusNasClientPingStatus[]>("/radius-nas-clients/ping-status/");
      setStatusById((prev) => {
        const next = { ...prev };
        res.data.forEach((entry) => {
          next[entry.id] = entry.status;
        });
        return next;
      });
    } catch {
      // Best-effort status indicator -- a failed check just leaves the
      // last known status (or "Checking…") in place rather than erroring
      // out the whole page.
    } finally {
      setStatusChecking(false);
    }
  }

  useEffect(() => {
    refreshStatuses();
    const interval = setInterval(refreshStatuses, NAS_STATUS_REFRESH_MS);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function openCreate() {
    setEditing(null);
    setForm(emptyNasForm);
    setShowModal(true);
  }

  function openEdit(client: RadiusNasClient) {
    setEditing(client);
    setForm({
      name: client.name,
      ip_address: client.ip_address,
      shortname: client.shortname,
      secret: "",
      realm: client.realm,
      description: client.description,
      is_active: client.is_active,
    });
    setShowModal(true);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      if (editing) {
        await api.patch(`/radius-nas-clients/${editing.id}/`, form);
      } else {
        await api.post("/radius-nas-clients/", form);
      }
      setShowModal(false);
      refetch();
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(client: RadiusNasClient) {
    if (
      !confirm(
        `Delete the RADIUS client "${client.name}" (${client.ip_address})? FreeRADIUS will stop accepting ` +
          "requests from this device until it's re-added and clients.conf is re-rendered. This can't be undone."
      )
    )
      return;
    try {
      await api.delete(`/radius-nas-clients/${client.id}/`);
      if (editing?.id === client.id) setShowModal(false);
      refetch();
    } catch (err: any) {
      alert(err?.response?.data?.detail || "Couldn't delete this RADIUS client.");
    }
  }

  const [pushingClient, setPushingClient] = useState<RadiusNasClient | null>(null);
  const [pushFreeradiusIp, setPushFreeradiusIp] = useState("");
  const [pushSaving, setPushSaving] = useState(false);
  const [pushResult, setPushResult] = useState<string | null>(null);
  const [pushError, setPushError] = useState<string | null>(null);
  // Stored default FreeRADIUS server IP (set in the admin-only panel at the
  // bottom of this tab) -- pre-fills the push modal below so staff don't
  // retype it on every push, while still leaving the field editable for a
  // one-off/secondary server.
  const [defaultFreeradiusIp, setDefaultFreeradiusIp] = useState("");

  useEffect(() => {
    // Admin-only endpoint -- non-admin staff can still
    // use RADIUS Clients/push, they just won't get a pre-filled default,
    // so a 403 here is expected and safely ignored rather than surfaced.
    api
      .get<OvpnSettingsConfig>("/ovpn-settings/")
      .then((res) => setDefaultFreeradiusIp(res.data.freeradius_ip))
      .catch(() => {});
  }, []);

  function openPush(client: RadiusNasClient) {
    setPushingClient(client);
    setPushFreeradiusIp(defaultFreeradiusIp);
    setPushResult(null);
    setPushError(null);
  }

  async function handlePushSubmit(e: FormEvent) {
    e.preventDefault();
    if (!pushingClient) return;
    setPushSaving(true);
    setPushError(null);
    setPushResult(null);
    try {
      const res = await api.post<{ status: string; device: string }>(
        `/radius-nas-clients/${pushingClient.id}/push-to-router/`,
        { freeradius_ip: pushFreeradiusIp }
      );
      setPushResult(`Pushed successfully to ${res.data.device}.`);
    } catch (err: any) {
      setPushError(err?.response?.data?.detail ?? "Couldn't push config to the router.");
    } finally {
      setPushSaving(false);
    }
  }

  return (
    <div>
      <div className="mb-3 rounded-md border border-[var(--border-hairline)] bg-[var(--tint-hover)] p-3 text-xs text-[var(--text-secondary)]">
        Devices allowed to send RADIUS requests to this platform's FreeRADIUS server (the Mikrotik at Teraco JHB and
        any others). Adding, editing or removing a client here applies to FreeRADIUS automatically within a few
        seconds — the change is validated first, and rejected rather than applied if it would break authentication.
        No SSH needed. The Status column is a live ping to each device's IP (auto-refreshed every 45s) — it confirms
        the device is reachable on the network, not that FreeRADIUS or RADIUS auth itself is working.
      </div>
      <div className="mb-4 flex items-center justify-end gap-3">
        <button
          type="button"
          className="text-sm text-[var(--series-1)] hover:underline disabled:opacity-50"
          onClick={() => refreshStatuses()}
          disabled={statusChecking}
        >
          {statusChecking ? "Checking status…" : "Refresh status"}
        </button>
        <ColumnToggle columns={NAS_COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["name"]} />
      </div>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Name</TH>
              {isVisible("status") && <TH>Status</TH>}
              {isVisible("ip_address") && <TH>IP address</TH>}
              {isVisible("shortname") && <TH>Shortname</TH>}
              {isVisible("secret") && <TH>Secret</TH>}
              {isVisible("realm") && <TH>Realm</TH>}
              {isVisible("active") && <TH>Active</TH>}
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((c) => (
              <TR key={c.id}>
                <TD className="font-medium">{c.name}</TD>
                {isVisible("status") && (
                  <TD>
                    {statusById[c.id] ? <StatusBadge status={statusById[c.id]} /> : <StatusBadge status="unknown" />}
                  </TD>
                )}
                {isVisible("ip_address") && <TD>{c.ip_address}</TD>}
                {isVisible("shortname") && <TD>{c.shortname}</TD>}
                {isVisible("secret") && <TD>{c.secret_set ? "•••••••• (set)" : "Not set"}</TD>}
                {isVisible("realm") && <TD>{c.realm || "—"}</TD>}
                {isVisible("active") && <TD>{c.is_active ? "Yes" : "No"}</TD>}
                <TD>
                  <div className="flex items-center gap-3">
                    <button className="text-[var(--series-1)] hover:underline" onClick={() => openEdit(c)}>
                      Edit
                    </button>
                    <button className="text-[var(--series-1)] hover:underline" onClick={() => openPush(c)}>
                      Push to router
                    </button>
                    <button
                      className="text-red-600 hover:underline dark:text-red-400"
                      onClick={() => handleDelete(c)}
                    >
                      Delete
                    </button>
                  </div>
                </TD>
              </TR>
            ))}
            {items.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]">No RADIUS clients configured yet.</TD>
              </TR>
            )}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title={editing ? "Edit RADIUS client" : "New RADIUS client"} onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Name">
              <input
                className={inputClass}
                required
                placeholder="Teraco JHB Mikrotik"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </FormField>
            <FormField label="IP address">
              <input
                className={inputClass}
                required
                placeholder="196.10.20.30"
                value={form.ip_address}
                onChange={(e) => setForm({ ...form, ip_address: e.target.value })}
              />
            </FormField>
            <FormField label="Shortname">
              <input
                className={inputClass}
                required
                placeholder="mikrotik-jhb"
                value={form.shortname}
                onChange={(e) => setForm({ ...form, shortname: e.target.value })}
                autoComplete="off"
                name="nas-shortname"
              />
            </FormField>
            <FormField label={`Shared secret${editing?.secret_set ? " (set — leave blank to keep)" : ""}`}>
              <input
                type="password"
                className={inputClass}
                placeholder={editing?.secret_set ? "••••••••" : "Set a shared secret"}
                value={form.secret}
                onChange={(e) => setForm({ ...form, secret: e.target.value })}
                autoComplete="new-password"
                name="nas-secret"
              />
            </FormField>
            <FormField label="Realm">
              <input
                className={inputClass}
                placeholder="e.g. jhb (optional — reporting/segmentation tag only)"
                value={form.realm}
                onChange={(e) => setForm({ ...form, realm: e.target.value })}
              />
            </FormField>
            <FormField label="Description">
              <input
                className={inputClass}
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
            </FormField>
            <FormField label="Active">
              <select
                className={inputClass}
                value={form.is_active ? "yes" : "no"}
                onChange={(e) => setForm({ ...form, is_active: e.target.value === "yes" })}
              >
                <option value="yes">Yes</option>
                <option value="no">No</option>
              </select>
            </FormField>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>Cancel</button>
              <button type="submit" disabled={saving} className={btnPrimary}>{saving ? "Saving…" : "Save"}</button>
            </div>
          </form>
        </Modal>
      )}

      {pushingClient && (
        <Modal title={`Push RADIUS config to router — ${pushingClient.name}`} onClose={() => setPushingClient(null)}>
          <form onSubmit={handlePushSubmit}>
            <p className="mb-3 text-xs text-[var(--text-muted)]">
              Pushes a <code className="font-mono">/radius</code> client entry (pointing at the FreeRADIUS server
              below, using this NAS client's secret) and <code className="font-mono">/ppp aaa use-radius=yes</code>{" "}
              directly to the router at {pushingClient.ip_address} via its Mikrotik API — only works if a router
              under Routers has the API enabled with a matching IP address. This does not set up the OVPN server,
              PPP profile, TLS certificate, or firewall — see deploy/radius/mikrotik_teraco_jhb.rsc for those.
            </p>
            <FormField label="FreeRADIUS server IP">
              <input
                className={inputClass}
                required
                placeholder="e.g. 154.65.111.61"
                value={pushFreeradiusIp}
                onChange={(e) => setPushFreeradiusIp(e.target.value)}
              />
            </FormField>
            {pushResult && <p className="mb-3 text-sm text-green-700 dark:text-green-400">{pushResult}</p>}
            {pushError && <p className="mb-3 text-sm text-red-700 dark:text-red-300">{pushError}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setPushingClient(null)}>Close</button>
              <button type="submit" disabled={pushSaving} className={btnPrimary}>{pushSaving ? "Pushing…" : "Push"}</button>
            </div>
          </form>
        </Modal>
      )}

      <DefaultFreeradiusServerPanel />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Default FreeRADIUS server -- admin-only. Was its own "OVPN" tab; folded
// into the bottom of RADIUS Clients since that's the only screen that uses
// it (it pre-fills "Push to router"), and a whole tab for one IP field was
// more navigation than the setting is worth.
// ---------------------------------------------------------------------------

const EMPTY_OVPN_SETTINGS_FORM = { freeradius_ip: "", notes: "" };

function DefaultFreeradiusServerPanel() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState(EMPTY_OVPN_SETTINGS_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  function load() {
    setLoading(true);
    api
      .get<OvpnSettingsConfig>("/ovpn-settings/")
      .then((r) => {
        setForm({ freeradius_ip: r.data.freeradius_ip, notes: r.data.notes });
        setLoading(false);
      })
      // Admin-only endpoint; a 403 for non-admin staff is expected. The
      // panel isn't rendered for them anyway, but the fetch must not be
      // left hanging on "Loading…" if roles change under it.
      .catch(() => setLoading(false));
  }

  useEffect(() => {
    if (isAdmin) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaved(false);
    setSaving(true);
    try {
      await api.patch<OvpnSettingsConfig>("/ovpn-settings/", form);
      setSaved(true);
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setError(typeof firstError === "string" ? firstError : "Could not save these settings — please try again.");
    } finally {
      setSaving(false);
    }
  }

  if (!isAdmin) return null;

  return (
    <div className="mt-8 border-t border-[var(--border-hairline)] pt-5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-sm font-medium text-[var(--series-1)] hover:underline"
      >
        {open ? "Hide" : "Show"} default FreeRADIUS server
      </button>

      {!open ? null : loading ? (
        <p className="mt-4 text-[var(--text-muted)]">Loading…</p>
      ) : (
      <>
      <p className="mb-4 mt-4 max-w-2xl text-sm text-[var(--text-secondary)]">
        The default FreeRADIUS server address that "Push to router" pre-fills, so staff don't have to type it by hand
        every time. Individual NAS devices and their shared secrets are the client rows above — this is only the
        default, and it stays editable per-push for a one-off or secondary server.
      </p>

      <form onSubmit={handleSubmit} className="max-w-2xl rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
        <FormField label="FreeRADIUS server IP">
          <input
            className={inputClass}
            placeholder="e.g. 154.65.111.61"
            value={form.freeradius_ip}
            onChange={(e) => setForm({ ...form, freeradius_ip: e.target.value })}
          />
        </FormField>
        <FormField label="Notes (staff-only, e.g. links or reminders)">
          <textarea
            className={inputClass}
            rows={4}
            value={form.notes}
            onChange={(e) => setForm({ ...form, notes: e.target.value })}
          />
        </FormField>

        {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
        {saved && !error && <p className="mb-3 text-sm text-[#0ca30c]">Settings saved.</p>}

        <div className="flex justify-end">
          <button type="submit" disabled={saving} className={btnPrimary}>
            {saving ? "Saving…" : "Save settings"}
          </button>
        </div>
      </form>
      </>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Activity log -- who did what, using which credentials.
//
// Read-only by construction, not by convention: there is no write endpoint
// behind this screen at all. Pruning old rows is done on the server with
// `prune_audit_log`, deliberately out of reach of a browser session belonging
// to somebody the log might be about.
// ---------------------------------------------------------------------------

const ACTIVITY_KINDS: { key: string; label: string }[] = [
  { key: "", label: "Everything" },
  { key: "changes", label: "Changes" },
  { key: "auth", label: "Sign-ins" },
];

function ActivityLogTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [count, setCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [kind, setKind] = useState("");
  const [actor, setActor] = useState("");
  const [search, setSearch] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [page, setPage] = useState(1);
  const { items: staff } = useApiList<User>("/staff-users/");

  useEffect(() => {
    onRegisterNewAction(null);
  }, [onRegisterNewAction]);

  // Any filter change puts you back on page 1. Staying on page 4 of a
  // narrower result set shows an empty screen that reads as "nothing
  // happened" rather than "you are past the end".
  useEffect(() => {
    setPage(1);
  }, [kind, actor, search, since, until]);

  useEffect(() => {
    const params = new URLSearchParams({ page: String(page), page_size: "50" });
    if (kind) params.set("kind", kind);
    if (actor) params.set("actor", actor);
    if (search) params.set("search", search);
    if (since) params.set("since", since);
    if (until) params.set("until", until);
    setLoading(true);
    api
      .get<Paginated<AuditEvent>>(`/audit-events/?${params.toString()}`)
      .then((res) => {
        setEvents(res.data.results);
        setCount(res.data.count);
      })
      .catch(() => setEvents([]))
      .finally(() => setLoading(false));
  }, [kind, actor, search, since, until, page]);

  const pages = Math.max(1, Math.ceil(count / 50));

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-end gap-2">
        <div className="flex gap-1 rounded-md bg-[var(--tint-subtle)] p-0.5">
          {ACTIVITY_KINDS.map((k) => (
            <button
              key={k.key}
              type="button"
              onClick={() => setKind(k.key)}
              className={`rounded px-3 py-1.5 text-xs font-medium transition-colors ${
                kind === k.key
                  ? "bg-[var(--surface-1)] text-[var(--text-primary)] shadow-sm"
                  : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
              }`}
            >
              {k.label}
            </button>
          ))}
        </div>

        <select className={filterSelectClass} value={actor} onChange={(e) => setActor(e.target.value)}>
          <option value="">Everyone</option>
          {staff.map((s) => (
            <option key={s.id} value={s.id}>
              {s.first_name || s.last_name ? `${s.first_name} ${s.last_name}`.trim() : s.username}
            </option>
          ))}
        </select>

        <label className="flex items-center gap-1 text-xs text-[var(--text-muted)]">
          From
          <input type="date" className={filterSelectClass} value={since} onChange={(e) => setSince(e.target.value)} />
        </label>
        <label className="flex items-center gap-1 text-xs text-[var(--text-muted)]">
          To
          <input type="date" className={filterSelectClass} value={until} onChange={(e) => setUntil(e.target.value)} />
        </label>

        <input
          className={inputClass}
          style={{ maxWidth: 240 }}
          placeholder="Search name or record…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)]">
        <ActivityList
          events={events}
          loading={loading}
          emptyMessage={
            since || until || actor || search || kind
              ? "Nothing matches those filters."
              : "Nothing recorded yet. Activity is logged from the moment this went live — anything done before that isn't here."
          }
        />
      </div>

      <div className="mt-3 flex items-center justify-between text-xs text-[var(--text-muted)]">
        <span>
          {count} event{count === 1 ? "" : "s"}
        </span>
        {pages > 1 && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              className={btnSecondary}
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              Previous
            </button>
            <span className="tabular-nums">
              Page {page} of {pages}
            </span>
            <button
              type="button"
              className={btnSecondary}
              disabled={page >= pages}
              onClick={() => setPage((p) => Math.min(pages, p + 1))}
            >
              Next
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
