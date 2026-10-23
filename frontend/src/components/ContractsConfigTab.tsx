import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import { Table, THead, TH, TR, TD } from "./Table";
import { StatusBadge } from "./StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "./Modal";
import { PdfPreviewModal } from "./PdfPreviewModal";
import { apiErrorMessage } from "../utils/apiError";
import type { ContractSettingsConfig, ContractTemplate } from "../types";

// Configs -> Contracts (6 Oct 2026): the contract templates and the
// automatic-send switches. A template is HTML with {{ placeholders }} filled
// from the client's profile when a contract is made; editing it never
// changes a contract already sent.

type Placeholder = { key: string; label: string };
type Form = { name: string; title: string; body_html: string; is_active: boolean };
const EMPTY: Form = { name: "", title: "Internet Service Agreement", body_html: "", is_active: true };

export function ContractsConfigTab({ onRegisterNewAction }: { onRegisterNewAction?: (a: null) => void }) {
  const [templates, setTemplates] = useState<ContractTemplate[]>([]);
  const [placeholders, setPlaceholders] = useState<Placeholder[]>([]);
  const [settings, setSettings] = useState<ContractSettingsConfig | null>(null);
  const [editing, setEditing] = useState<ContractTemplate | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState<Form>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [settingsMsg, setSettingsMsg] = useState("");
  const [preview, setPreview] = useState<ContractTemplate | null>(null);

  useEffect(() => onRegisterNewAction?.(null), [onRegisterNewAction]);

  function load() {
    api.get<ContractTemplate[]>("/contract-templates/").then((r) => setTemplates(r.data));
    api.get<ContractSettingsConfig>("/contract-settings/").then((r) => setSettings(r.data));
  }

  useEffect(() => {
    load();
    api.get<Placeholder[]>("/contract-templates/placeholders/").then((r) => setPlaceholders(r.data));
  }, []);

  function openNew() {
    setEditing(null);
    setForm(EMPTY);
    setError("");
    setShowModal(true);
  }

  function openEdit(t: ContractTemplate) {
    setEditing(t);
    setForm({ name: t.name, title: t.title, body_html: t.body_html, is_active: t.is_active });
    setError("");
    setShowModal(true);
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      if (editing) await api.patch(`/contract-templates/${editing.id}/`, form);
      else await api.post("/contract-templates/", form);
      setShowModal(false);
      load();
    } catch (err) {
      setError(apiErrorMessage(err, "Couldn't save the template."));
    } finally {
      setSaving(false);
    }
  }

  async function remove(t: ContractTemplate) {
    if (!window.confirm(`Delete the template "${t.name}"?`)) return;
    try {
      await api.delete(`/contract-templates/${t.id}/`);
      load();
    } catch (err) {
      window.alert(apiErrorMessage(err, "Couldn't delete it."));
    }
  }

  async function saveSettings(patch: Partial<ContractSettingsConfig>) {
    setSettingsMsg("");
    try {
      const res = await api.patch<ContractSettingsConfig>("/contract-settings/", patch);
      setSettings(res.data);
      setSettingsMsg("Saved.");
    } catch (err) {
      setSettingsMsg(apiErrorMessage(err, "Couldn't save."));
    }
  }

  function insert(key: string) {
    setForm((f) => ({ ...f, body_html: `${f.body_html}{{ ${key} }}` }));
  }

  return (
    <div className="space-y-6">
      {settings && (
        <section className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4">
          <h2 className="mb-3 text-sm font-semibold">Sending</h2>
          <div className="grid gap-4 text-sm sm:grid-cols-2">
            <FormField label="Default template">
              <select
                className={inputClass}
                value={settings.default_template ?? ""}
                onChange={(e) => saveSettings({ default_template: e.target.value ? Number(e.target.value) : null })}
              >
                <option value="">— none —</option>
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                    {t.is_active ? "" : " (switched off)"}
                  </option>
                ))}
              </select>
            </FormField>
            <FormField label="Signing link valid for (days)">
              <input
                type="number"
                min={1}
                max={90}
                className={inputClass}
                defaultValue={settings.link_valid_days}
                onBlur={(e) => {
                  const days = Number(e.target.value);
                  if (days && days !== settings.link_valid_days) saveSettings({ link_valid_days: days });
                }}
              />
            </FormField>
          </div>
          <div className="mt-3 space-y-2 text-sm">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={settings.auto_send_new_client}
                onChange={(e) => saveSettings({ auto_send_new_client: e.target.checked })}
              />
              Send the default template automatically to every new client (New client form)
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={settings.auto_send_lead_accepted}
                onChange={(e) => saveSettings({ auto_send_lead_accepted: e.target.checked })}
              />
              Send it automatically when a sales lead is accepted
            </label>
            <p className="text-xs text-[var(--text-muted)]">
              A client who already has a contract waiting or signed isn't sent another. A client with no email gets
              theirs saved as "Not sent" — sign it on a device, or email it once you have an address. Staff can always
              send one by hand from the client's Contracts tab.
            </p>
            {settingsMsg && <p className="text-xs text-[var(--text-secondary)]">{settingsMsg}</p>}
          </div>
        </section>
      )}

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Templates</h2>
          <button className={btnPrimary} onClick={openNew}>
            + New template
          </button>
        </div>
        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)]">
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Heading</TH>
                <TH>Status</TH>
                <TH>Contracts made</TH>
                <TH />
              </TR>
            </THead>
            <tbody>
              {templates.map((t) => (
                <TR key={t.id}>
                  <TD>{t.name}</TD>
                  <TD>{t.title}</TD>
                  <TD>
                    <StatusBadge status={t.is_active ? "active" : "inactive"} label={t.is_active ? "On" : "Off"} />
                  </TD>
                  <TD className="tabular-nums">{t.contract_count}</TD>
                  <TD className="whitespace-nowrap text-right">
                    <button className="mr-3 text-xs font-medium text-[var(--series-1)] hover:underline" onClick={() => setPreview(t)}>
                      Preview
                    </button>
                    <button className="mr-3 text-xs font-medium text-[var(--series-1)] hover:underline" onClick={() => openEdit(t)}>
                      Edit
                    </button>
                    {t.contract_count === 0 && (
                      <button className="text-xs font-medium text-[var(--status-critical)] hover:underline" onClick={() => remove(t)}>
                        Delete
                      </button>
                    )}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </div>
      </section>

      {showModal && (
        <Modal title={editing ? `Edit ${editing.name}` : "New contract template"} onClose={() => setShowModal(false)}>
          <form onSubmit={save} className="space-y-3">
            <FormField label="Name (for staff)" required>
              <input className={inputClass} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
            </FormField>
            <FormField label="Heading on the contract" required>
              <input className={inputClass} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} required />
            </FormField>
            <FormField label="Wording (HTML)" required>
              <textarea
                className={`${inputClass} h-64 font-mono text-xs`}
                value={form.body_html}
                onChange={(e) => setForm({ ...form, body_html: e.target.value })}
                required
              />
            </FormField>
            <div>
              <p className="mb-1 text-xs font-medium text-[var(--text-secondary)]">Click to add a placeholder</p>
              <div className="flex max-h-28 flex-wrap gap-1 overflow-y-auto">
                {placeholders.map((p) => (
                  <button
                    key={p.key}
                    type="button"
                    title={p.label}
                    onClick={() => insert(p.key)}
                    className="rounded border border-[var(--border-hairline)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-secondary)] hover:bg-[var(--tint-hover)]"
                  >
                    {p.key}
                  </button>
                ))}
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
              Switched on (can be sent to clients)
            </label>
            {editing && editing.contract_count > 0 && (
              <p className="text-xs text-[var(--text-muted)]">
                The {editing.contract_count} contract(s) already made keep the wording they were sent with.
              </p>
            )}
            {error && <p className="text-sm text-[var(--status-critical)]">{error}</p>}
            <div className="flex justify-end gap-2">
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

      {preview && (
        <PdfPreviewModal
          title={`${preview.name} — preview with sample details`}
          url={`/contract-templates/${preview.id}/preview/`}
          filename={`${preview.name} preview.pdf`}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  );
}
