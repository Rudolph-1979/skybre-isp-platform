import { useEffect, useState, type FormEvent } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../../api/client";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import type { Customer, Service, Invoice, Payment, Ticket, User, EmailTemplateKey, EmailLog } from "../../types";

type Tab = "overview" | "email";

const TABS: { key: Tab; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "email", label: "Email" },
];

const TEMPLATE_OPTIONS: { key: EmailTemplateKey; label: string }[] = [
  { key: "welcome", label: "Welcome message" },
  { key: "statement", label: "Statement" },
  { key: "invoice", label: "Invoice" },
  { key: "payment_reminder", label: "Payment reminder" },
  { key: "suspension", label: "Suspension notification" },
];

export function CustomerDetailPage() {
  const { id } = useParams();
  const [tab, setTab] = useState<Tab>("overview");
  const [copied, setCopied] = useState(false);
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [services, setServices] = useState<Service[]>([]);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [staff, setStaff] = useState<User[]>([]);

  const [showEdit, setShowEdit] = useState(false);
  const [form, setForm] = useState<Partial<Customer>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const [emailLogs, setEmailLogs] = useState<EmailLog[]>([]);
  const [sendTemplate, setSendTemplate] = useState<EmailTemplateKey | "">("");
  const [sendInvoiceId, setSendInvoiceId] = useState("");
  const [preview, setPreview] = useState<{ subject: string; body_html: string; will_attach_pdf: boolean } | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [sending, setSending] = useState(false);
  const [sendResult, setSendResult] = useState<{ ok: boolean; message: string } | null>(null);

  function refetchCustomer() {
    if (!id) return;
    api.get<Customer>(`/customers/${id}/`).then((res) => setCustomer(res.data));
  }

  function refetchEmailLogs() {
    if (!id) return;
    api.get<{ results: EmailLog[] }>(`/email-logs/?customer=${id}`).then((res) => setEmailLogs(res.data.results));
  }

  useEffect(() => {
    if (!id) return;
    refetchCustomer();
    api.get<{ results: Service[] }>(`/services/?customer=${id}`).then((res) => setServices(res.data.results));
    api.get<{ results: Invoice[] }>(`/invoices/?customer=${id}&ordering=-date_created`).then((res) => setInvoices(res.data.results));
    api.get<{ results: Payment[] }>(`/payments/?customer=${id}&ordering=-date`).then((res) => setPayments(res.data.results));
    api.get<{ results: Ticket[] }>(`/tickets/?customer=${id}`).then((res) => setTickets(res.data.results));
    api.get<{ results: User[] }>("/staff-users/?page_size=100").then((res) => setStaff(res.data.results));
    refetchEmailLogs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  function selectTemplate(key: EmailTemplateKey | "") {
    setSendTemplate(key);
    setSendInvoiceId("");
    setPreview(null);
    setSendResult(null);
  }

  async function handlePreview() {
    if (!id || !sendTemplate) return;
    setPreviewing(true);
    setSendResult(null);
    try {
      const res = await api.post("/email-preview/", {
        template_key: sendTemplate,
        customer: Number(id),
        invoice: sendTemplate === "invoice" ? Number(sendInvoiceId) : undefined,
      });
      setPreview(res.data);
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setSendResult({ ok: false, message: typeof firstError === "string" ? firstError : "Could not render a preview." });
    } finally {
      setPreviewing(false);
    }
  }

  async function handleSend() {
    if (!id || !sendTemplate) return;
    setSending(true);
    setSendResult(null);
    try {
      await api.post(`/customers/${id}/send-email/`, {
        template_key: sendTemplate,
        invoice: sendTemplate === "invoice" ? Number(sendInvoiceId) : undefined,
      });
      setSendResult({ ok: true, message: "Email sent." });
      setPreview(null);
      refetchEmailLogs();
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setSendResult({
        ok: false,
        message: typeof firstError === "string" ? firstError : "Could not send this email — see the log below for details.",
      });
      refetchEmailLogs();
    } finally {
      setSending(false);
    }
  }

  function openEdit() {
    if (!customer) return;
    setForm({
      full_name: customer.full_name,
      company_name: customer.company_name,
      email: customer.email,
      phone: customer.phone,
      address: customer.address,
      city: customer.city,
      zip_code: customer.zip_code,
      customer_type: customer.customer_type,
      category: customer.category,
      status: customer.status,
      assigned_staff: customer.assigned_staff,
      notes: customer.notes,
    });
    setError("");
    setShowEdit(true);
  }

  async function handleSave(e: FormEvent) {
    e.preventDefault();
    if (!id) return;
    setSaving(true);
    setError("");
    try {
      await api.patch(`/customers/${id}/`, form);
      setShowEdit(false);
      refetchCustomer();
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setError(typeof firstError === "string" ? firstError : "Could not save changes — please check the fields and try again.");
    } finally {
      setSaving(false);
    }
  }

  async function copyEmail() {
    if (!customer?.email) return;
    try {
      await navigator.clipboard.writeText(customer.email);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be blocked by the browser (e.g. no HTTPS,
      // no permission) — the mailto link below still works either way.
    }
  }

  if (!customer) return <p className="text-[var(--text-muted)]">Loading…</p>;

  return (
    <div>
      <Link to="/admin/customers" className="mb-4 inline-block text-sm text-[var(--series-1)] hover:underline">
        ← Back to customers
      </Link>
      <PageHeader
        title={customer.full_name}
        subtitle={`${customer.customer_id} · ${customer.email} · ${customer.phone}`}
        actions={
          <>
            <StatusBadge status={customer.status} />
            <button className={btnPrimary} onClick={openEdit}>
              Edit customer
            </button>
          </>
        }
      />

      <div className="mb-6 flex gap-1 border-b border-[var(--border-hairline)]">
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

      {tab === "overview" && (
        <>
          <div className="mb-6 grid grid-cols-3 gap-4 text-sm">
            <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4">
              <p className="text-[var(--text-muted)]">Balance owed</p>
              <p className="tabular-nums mt-1 text-lg font-semibold">R {parseFloat(customer.balance).toFixed(2)}</p>
            </div>
            <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4">
              <p className="text-[var(--text-muted)]">Address</p>
              <p className="mt-1">{customer.address || "—"}, {customer.city}</p>
            </div>
            <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4">
              <p className="text-[var(--text-muted)]">Assigned staff</p>
              <p className="mt-1">{customer.assigned_staff_name ?? "Unassigned"}</p>
            </div>
          </div>

          <h2 className="mb-2 text-sm font-semibold">Services</h2>
          <Table>
            <THead>
              <tr>
                <TH>Tariff</TH>
                <TH>Price</TH>
                <TH>Status</TH>
                <TH>Start date</TH>
              </tr>
            </THead>
            <tbody>
              {services.map((s) => (
                <TR key={s.id}>
                  <TD>{s.tariff_name}</TD>
                  <TD className="tabular-nums">R {parseFloat(s.price).toFixed(2)}</TD>
                  <TD><StatusBadge status={s.status} /></TD>
                  <TD>{s.start_date ?? "—"}</TD>
                </TR>
              ))}
              {services.length === 0 && <TR><TD className="text-[var(--text-muted)]">No services yet.</TD></TR>}
            </tbody>
          </Table>

          <h2 className="mb-2 mt-6 text-sm font-semibold">Invoices</h2>
          <Table>
            <THead>
              <tr>
                <TH>Number</TH>
                <TH>Due</TH>
                <TH>Total</TH>
                <TH>Paid</TH>
                <TH>Status</TH>
              </tr>
            </THead>
            <tbody>
              {invoices.map((inv) => (
                <TR key={inv.id}>
                  <TD><Link to={`/admin/invoices/${inv.id}`} className="text-[var(--series-1)] hover:underline">{inv.number}</Link></TD>
                  <TD>{inv.date_due}</TD>
                  <TD className="tabular-nums">R {parseFloat(inv.total).toFixed(2)}</TD>
                  <TD className="tabular-nums">R {parseFloat(inv.paid_amount).toFixed(2)}</TD>
                  <TD><StatusBadge status={inv.status} /></TD>
                </TR>
              ))}
              {invoices.length === 0 && <TR><TD className="text-[var(--text-muted)]">No invoices yet.</TD></TR>}
            </tbody>
          </Table>

          <h2 className="mb-2 mt-6 text-sm font-semibold">Payments</h2>
          <Table>
            <THead>
              <tr>
                <TH>Date</TH>
                <TH>Amount</TH>
                <TH>Method</TH>
                <TH>Received by</TH>
              </tr>
            </THead>
            <tbody>
              {payments.map((p) => (
                <TR key={p.id}>
                  <TD>{new Date(p.date).toLocaleDateString()}</TD>
                  <TD className="tabular-nums">R {parseFloat(p.amount).toFixed(2)}</TD>
                  <TD className="capitalize">{p.method.replace("_", " ")}</TD>
                  <TD>{p.received_by_name ?? "—"}</TD>
                </TR>
              ))}
              {payments.length === 0 && <TR><TD className="text-[var(--text-muted)]">No payments yet.</TD></TR>}
            </tbody>
          </Table>

          <h2 className="mb-2 mt-6 text-sm font-semibold">Tickets</h2>
          <Table>
            <THead>
              <tr>
                <TH>Ticket</TH>
                <TH>Subject</TH>
                <TH>Status</TH>
                <TH>Priority</TH>
              </tr>
            </THead>
            <tbody>
              {tickets.map((t) => (
                <TR key={t.id}>
                  <TD><Link to={`/admin/tickets/${t.id}`} className="text-[var(--series-1)] hover:underline">{t.ticket_number}</Link></TD>
                  <TD>{t.subject}</TD>
                  <TD><StatusBadge status={t.status} /></TD>
                  <TD><StatusBadge status={t.priority} /></TD>
                </TR>
              ))}
              {tickets.length === 0 && <TR><TD className="text-[var(--text-muted)]">No tickets yet.</TD></TR>}
            </tbody>
          </Table>
        </>
      )}

      {tab === "email" && (
        <div className="space-y-6">
          <div className="max-w-md rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
            {customer.email ? (
              <>
                <p className="text-sm text-[var(--text-muted)]">Email address</p>
                <p className="mt-1 mb-4 text-lg font-medium text-[var(--text-primary)]">{customer.email}</p>
                <div className="flex gap-2">
                  <a href={`mailto:${customer.email}`} className={btnPrimary}>
                    Send email
                  </a>
                  <button type="button" className={btnSecondary} onClick={copyEmail}>
                    {copied ? "Copied!" : "Copy address"}
                  </button>
                </div>
              </>
            ) : (
              <p className="text-sm text-[var(--text-muted)]">
                No email address on file for this customer.{" "}
                <button type="button" className="text-[var(--series-1)] hover:underline" onClick={openEdit}>
                  Add one
                </button>
                .
              </p>
            )}
          </div>

          {customer.email && (
            <div className="max-w-2xl rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
              <h2 className="mb-3 text-sm font-semibold">Send a templated email</h2>
              <div className="flex flex-wrap items-end gap-3">
                <FormField label="Template">
                  <select
                    className={inputClass}
                    value={sendTemplate}
                    onChange={(e) => selectTemplate(e.target.value as EmailTemplateKey | "")}
                  >
                    <option value="">Choose a template…</option>
                    {TEMPLATE_OPTIONS.map((t) => (
                      <option key={t.key} value={t.key}>{t.label}</option>
                    ))}
                  </select>
                </FormField>
                {sendTemplate === "invoice" && (
                  <FormField label="Invoice">
                    <select
                      className={inputClass}
                      value={sendInvoiceId}
                      onChange={(e) => {
                        setSendInvoiceId(e.target.value);
                        setPreview(null);
                      }}
                    >
                      <option value="">Choose an invoice…</option>
                      {invoices.map((inv) => (
                        <option key={inv.id} value={inv.id}>{inv.number} — R {parseFloat(inv.total).toFixed(2)}</option>
                      ))}
                    </select>
                  </FormField>
                )}
                <button
                  type="button"
                  className={btnSecondary}
                  disabled={!sendTemplate || (sendTemplate === "invoice" && !sendInvoiceId) || previewing}
                  onClick={handlePreview}
                >
                  {previewing ? "Loading…" : "Preview"}
                </button>
                <button
                  type="button"
                  className={btnPrimary}
                  disabled={!sendTemplate || (sendTemplate === "invoice" && !sendInvoiceId) || sending}
                  onClick={handleSend}
                >
                  {sending ? "Sending…" : "Send"}
                </button>
              </div>

              {sendResult && (
                <p className={`mt-3 text-sm ${sendResult.ok ? "text-[var(--status-good)]" : "text-[var(--status-critical)]"}`}>
                  {sendResult.message}
                </p>
              )}

              {preview && (
                <div className="mt-4 rounded-md border border-[var(--border-hairline)] bg-[var(--tint-subtle)] p-4">
                  <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Subject</p>
                  <p className="mb-3 font-medium text-[var(--text-primary)]">{preview.subject}</p>
                  <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Body</p>
                  <div
                    className="mt-1 rounded bg-[var(--surface-1)] p-3 text-sm text-[var(--text-primary)]"
                    dangerouslySetInnerHTML={{ __html: preview.body_html }}
                  />
                  {preview.will_attach_pdf && (
                    <p className="mt-2 text-xs text-[var(--text-muted)]">A PDF will be attached to this email.</p>
                  )}
                </div>
              )}
            </div>
          )}

          <div>
            <h2 className="mb-2 text-sm font-semibold">Sent emails</h2>
            <Table>
              <THead>
                <tr>
                  <TH>Date</TH>
                  <TH>Template</TH>
                  <TH>Subject</TH>
                  <TH>Status</TH>
                </tr>
              </THead>
              <tbody>
                {emailLogs.map((log) => (
                  <TR key={log.id}>
                    <TD>{new Date(log.created_at).toLocaleString()}</TD>
                    <TD>{log.template_name}</TD>
                    <TD className="max-w-xs truncate">
                      <span title={log.status === "failed" ? log.error_message : log.subject}>
                        {log.subject || <span className="text-[var(--text-muted)]">—</span>}
                      </span>
                    </TD>
                    <TD>
                      <StatusBadge status={log.status} />
                      {log.status === "failed" && log.error_message && (
                        <span className="ml-2 text-xs text-[var(--text-muted)]">{log.error_message}</span>
                      )}
                    </TD>
                  </TR>
                ))}
                {emailLogs.length === 0 && <TR><TD className="text-[var(--text-muted)]">No emails sent to this customer yet.</TD></TR>}
              </tbody>
            </Table>
          </div>
        </div>
      )}

      {showEdit && (
        <Modal title="Edit customer" onClose={() => setShowEdit(false)}>
          <form onSubmit={handleSave}>
            <FormField label="Full name">
              <input
                className={inputClass}
                required
                value={form.full_name ?? ""}
                onChange={(e) => setForm({ ...form, full_name: e.target.value })}
              />
            </FormField>
            <FormField label="Company name">
              <input
                className={inputClass}
                value={form.company_name ?? ""}
                onChange={(e) => setForm({ ...form, company_name: e.target.value })}
              />
            </FormField>
            <FormField label="Email">
              <input
                type="email"
                className={inputClass}
                value={form.email ?? ""}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />
            </FormField>
            <FormField label="Phone">
              <input
                className={inputClass}
                value={form.phone ?? ""}
                onChange={(e) => setForm({ ...form, phone: e.target.value })}
              />
            </FormField>
            <FormField label="Address">
              <input
                className={inputClass}
                value={form.address ?? ""}
                onChange={(e) => setForm({ ...form, address: e.target.value })}
              />
            </FormField>
            <div className="grid grid-cols-2 gap-3">
              <FormField label="City">
                <input
                  className={inputClass}
                  value={form.city ?? ""}
                  onChange={(e) => setForm({ ...form, city: e.target.value })}
                />
              </FormField>
              <FormField label="Zip code">
                <input
                  className={inputClass}
                  value={form.zip_code ?? ""}
                  onChange={(e) => setForm({ ...form, zip_code: e.target.value })}
                />
              </FormField>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <FormField label="Customer type">
                <select
                  className={inputClass}
                  value={form.customer_type ?? "individual"}
                  onChange={(e) => setForm({ ...form, customer_type: e.target.value as Customer["customer_type"] })}
                >
                  <option value="individual">Individual</option>
                  <option value="company">Company</option>
                </select>
              </FormField>
              <FormField label="Category">
                <select
                  className={inputClass}
                  value={form.category ?? "residential"}
                  onChange={(e) => setForm({ ...form, category: e.target.value as Customer["category"] })}
                >
                  <option value="residential">Residential</option>
                  <option value="business">Business</option>
                </select>
              </FormField>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <FormField label="Status">
                <select
                  className={inputClass}
                  value={form.status ?? "new"}
                  onChange={(e) => setForm({ ...form, status: e.target.value as Customer["status"] })}
                >
                  <option value="new">New</option>
                  <option value="active">Active</option>
                  <option value="suspended">Suspended</option>
                  <option value="inactive">Inactive</option>
                </select>
              </FormField>
              <FormField label="Assigned staff">
                <select
                  className={inputClass}
                  value={form.assigned_staff ?? ""}
                  onChange={(e) => setForm({ ...form, assigned_staff: e.target.value ? Number(e.target.value) : null })}
                >
                  <option value="">Unassigned</option>
                  {staff.map((s) => (
                    <option key={s.id} value={s.id}>{s.first_name || s.username}</option>
                  ))}
                </select>
              </FormField>
            </div>
            <FormField label="Notes">
              <textarea
                className={inputClass}
                rows={3}
                value={form.notes ?? ""}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
              />
            </FormField>

            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowEdit(false)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : "Save changes"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
