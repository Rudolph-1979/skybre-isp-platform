import { useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import type { EmailTemplate } from "../../types";

// Shown to staff editing a template so they know which {{ placeholders }}
// are available — kept in sync with notifications/services.py's
// build_context() on the backend.
const COMMON_PLACEHOLDERS = [
  "company_name", "customer_name", "customer_id", "customer_email", "portal_url", "balance", "today",
];
const EXTRA_PLACEHOLDERS: Record<string, string[]> = {
  statement: ["statement_date"],
  invoice: ["invoice_number", "invoice_total", "invoice_due_date"],
  payment_reminder: ["invoice_number (blank if none)", "invoice_due_date (blank if none)"],
};

export function EmailTemplatesPage() {
  const { items, loading, refetch } = useApiList<EmailTemplate>("/email-templates/?page_size=25");
  const [editing, setEditing] = useState<EmailTemplate | null>(null);
  const [form, setForm] = useState({ subject: "", body_html: "" });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

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
      <PageHeader
        title="Email Templates"
        subtitle="Customize the wording sent for each type of customer email. Use {{ placeholder }} syntax to insert customer/invoice details."
      />

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
