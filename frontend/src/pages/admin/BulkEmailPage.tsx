import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { inputClass, filterSelectClass, btnPrimary, btnSecondary } from "../../components/Modal";
import EmailBodyPreview from "../../components/EmailBodyPreview";
import type { Customer, EmailTemplateKey, EmailLog } from "../../types";

const PAGE_SIZE = 50;

// "invoice", "quote", "proforma", and "payment_received" are deliberately
// excluded from the Send tab's own dropdown — each is tied to one specific
// document/payment, which doesn't make sense picked once across many
// different customers.
const TEMPLATE_OPTIONS: { key: EmailTemplateKey; label: string }[] = [
  { key: "welcome", label: "Welcome message" },
  { key: "statement", label: "Statement" },
  { key: "payment_reminder", label: "Payment reminder" },
  { key: "suspension", label: "Suspension notification" },
];

// The full set, for the Sent Email tab's filter -- that log includes every
// kind of email sent platform-wide (bulk sends and individual document
// sends alike), not just the bulk-eligible ones above.
const ALL_TEMPLATE_LABELS: Record<EmailTemplateKey, string> = {
  welcome: "Welcome message",
  quote: "Quote",
  proforma: "Pro forma invoice",
  statement: "Statement",
  invoice: "Invoice",
  payment_reminder: "Payment reminder",
  suspension: "Suspension notification",
  payment_received: "Payment received",
};

type Tab = "send" | "sent-email";

export function BulkEmailPage() {
  const [tab, setTab] = useState<Tab>("send");

  const TABS: { key: Tab; label: string }[] = [
    { key: "send", label: "Send" },
    { key: "sent-email", label: "Sent Email" },
  ];

  return (
    <div>
      <PageHeader
        title="Email"
        subtitle="Send templated emails to customers and review everything that's gone out."
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
      {tab === "send" && <SendTab />}
      {tab === "sent-email" && <SentEmailTab />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Send (bulk send to filtered/selected customers)
// ---------------------------------------------------------------------------

function SendTab() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [overdueOnly, setOverdueOnly] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());

  const [template, setTemplate] = useState<EmailTemplateKey | "">("");
  const [preview, setPreview] = useState<{ subject: string; body_html: string } | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [sending, setSending] = useState(false);
  const [sendResult, setSendResult] = useState<{ batchId: string; queuedCount: number } | null>(null);
  const [batchLogs, setBatchLogs] = useState<EmailLog[] | null>(null);
  const [checkingResults, setCheckingResults] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
    }, 300);
    return () => clearTimeout(t);
  }, [search]);

  const filterQuery = `${debouncedSearch ? `&search=${encodeURIComponent(debouncedSearch)}` : ""}${
    statusFilter ? `&status=${statusFilter}` : ""
  }${categoryFilter ? `&category=${categoryFilter}` : ""}${typeFilter ? `&customer_type=${typeFilter}` : ""}${
    overdueOnly ? "&overdue=true" : ""
  }`;

  const url = `/customers/?page_size=${PAGE_SIZE}&page=${page}&ordering=full_name${filterQuery}`;
  const { items, count, loading } = useApiList<Customer>(url);

  const totalPages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const emailableOnPage = items.filter((c) => c.email);

  function toggleOne(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function togglePage() {
    const allSelected = emailableOnPage.every((c) => selected.has(c.id));
    setSelected((prev) => {
      const next = new Set(prev);
      emailableOnPage.forEach((c) => (allSelected ? next.delete(c.id) : next.add(c.id)));
      return next;
    });
  }

  async function selectAllMatching() {
    const res = await api.get<{ results: Customer[] }>(`/customers/?page_size=${count || 1000}${filterQuery}`);
    setSelected(new Set(res.data.results.filter((c) => c.email).map((c) => c.id)));
  }

  function clearSelection() {
    setSelected(new Set());
  }

  async function handlePreview() {
    if (!template || selected.size === 0) return;
    setPreviewing(true);
    setError("");
    try {
      const sampleId = [...selected][0];
      const res = await api.post("/email-preview/", { template_key: template, customer: sampleId });
      setPreview(res.data);
    } catch {
      setError("Could not render a preview.");
    } finally {
      setPreviewing(false);
    }
  }

  async function handleSend() {
    if (!template || selected.size === 0) return;
    setSending(true);
    setError("");
    setSendResult(null);
    setBatchLogs(null);
    try {
      const res = await api.post("/bulk-email/", {
        template_key: template,
        customer_ids: [...selected],
      });
      setSendResult({ batchId: res.data.batch_id, queuedCount: res.data.queued_count });
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setError(typeof firstError === "string" ? firstError : "Could not start the bulk send.");
    } finally {
      setSending(false);
    }
  }

  async function checkResults() {
    if (!sendResult) return;
    setCheckingResults(true);
    try {
      const res = await api.get<{ results: EmailLog[] }>(`/email-logs/?batch_id=${sendResult.batchId}&page_size=500`);
      setBatchLogs(res.data.results);
    } finally {
      setCheckingResults(false);
    }
  }

  const selectedCount = selected.size;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <input
          className={`${inputClass} max-w-xs`}
          placeholder="Search customers…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select className={filterSelectClass} value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}>
          <option value="">All statuses</option>
          <option value="new">New</option>
          <option value="active">Active</option>
          <option value="suspended">Suspended</option>
          <option value="inactive">Inactive</option>
        </select>
        <select className={filterSelectClass} value={categoryFilter} onChange={(e) => { setCategoryFilter(e.target.value); setPage(1); }}>
          <option value="">All categories</option>
          <option value="residential">Residential</option>
          <option value="business">Business</option>
        </select>
        <select className={filterSelectClass} value={typeFilter} onChange={(e) => { setTypeFilter(e.target.value); setPage(1); }}>
          <option value="">All types</option>
          <option value="individual">Individual</option>
          <option value="company">Company</option>
        </select>
        <label className="flex items-center gap-2 rounded-md border border-[var(--baseline)] bg-[var(--surface-2)] px-3 py-2 text-sm text-[var(--text-primary)]">
          <input
            type="checkbox"
            checked={overdueOnly}
            onChange={(e) => { setOverdueOnly(e.target.checked); setPage(1); }}
          />
          Overdue only
        </label>
        {(statusFilter || categoryFilter || typeFilter || overdueOnly || search) && (
          <button
            type="button"
            className={btnSecondary}
            onClick={() => { setStatusFilter(""); setCategoryFilter(""); setTypeFilter(""); setOverdueOnly(false); setSearch(""); }}
          >
            Clear filters
          </button>
        )}
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
        <button type="button" className={btnSecondary} onClick={selectAllMatching}>
          Select all {count} matching
        </button>
        <button type="button" className={btnSecondary} onClick={clearSelection}>
          Clear selection
        </button>
        <span className="text-[var(--text-muted)]">{selectedCount} selected</span>
      </div>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <>
          <Table>
            <THead>
              <tr>
                <TH>
                  <input
                    type="checkbox"
                    checked={emailableOnPage.length > 0 && emailableOnPage.every((c) => selected.has(c.id))}
                    onChange={togglePage}
                  />
                </TH>
                <TH>Customer</TH>
                <TH>Email</TH>
                <TH>Status</TH>
              </tr>
            </THead>
            <tbody>
              {items.map((c) => (
                <TR key={c.id}>
                  <TD>
                    <input
                      type="checkbox"
                      disabled={!c.email}
                      checked={selected.has(c.id)}
                      onChange={() => toggleOne(c.id)}
                    />
                  </TD>
                  <TD>
                    <div className="font-medium">{c.full_name}</div>
                    <div className="text-xs text-[var(--text-muted)]">{c.customer_id}</div>
                  </TD>
                  <TD>{c.email || <span className="text-[var(--text-muted)]">No email on file</span>}</TD>
                  <TD><StatusBadge status={c.status} /></TD>
                </TR>
              ))}
            </tbody>
          </Table>

          <div className="mt-3 flex items-center justify-between text-sm text-[var(--text-muted)]">
            <span>
              Showing {items.length === 0 ? 0 : (page - 1) * PAGE_SIZE + 1}–{(page - 1) * PAGE_SIZE + items.length} of {count}
            </span>
            <div className="flex gap-2">
              <button type="button" className={btnSecondary} disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                Previous
              </button>
              <span className="px-2 py-2">Page {page} of {totalPages}</span>
              <button type="button" className={btnSecondary} disabled={page >= totalPages} onClick={() => setPage((p) => Math.min(totalPages, p + 1))}>
                Next
              </button>
            </div>
          </div>
        </>
      )}

      <div className="mt-8 max-w-2xl rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
        <h2 className="mb-3 text-sm font-semibold">Send</h2>
        <div className="flex flex-wrap items-end gap-3">
          <label className="block text-sm">
            <span className="mb-1 block font-medium text-[var(--text-secondary)]">Template</span>
            <select
              className={inputClass}
              value={template}
              onChange={(e) => { setTemplate(e.target.value as EmailTemplateKey | ""); setPreview(null); setSendResult(null); }}
            >
              <option value="">Choose a template…</option>
              {TEMPLATE_OPTIONS.map((t) => (
                <option key={t.key} value={t.key}>{t.label}</option>
              ))}
            </select>
          </label>
          <button type="button" className={btnSecondary} disabled={!template || selectedCount === 0 || previewing} onClick={handlePreview}>
            {previewing ? "Loading…" : "Preview"}
          </button>
          <button type="button" className={btnPrimary} disabled={!template || selectedCount === 0 || sending} onClick={handleSend}>
            {sending ? "Sending…" : `Send to ${selectedCount || ""} customer${selectedCount === 1 ? "" : "s"}`}
          </button>
        </div>

        {error && <p className="mt-3 text-sm text-[var(--status-critical)]">{error}</p>}

        {preview && (
          <div className="mt-4 rounded-md border border-[var(--border-hairline)] bg-[var(--tint-subtle)] p-4">
            <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Subject (rendered for one sample recipient)</p>
            <p className="mb-3 font-medium text-[var(--text-primary)]">{preview.subject}</p>
            <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Body</p>
            <EmailBodyPreview html={preview.body_html} />
          </div>
        )}

        {sendResult && (
          <div className="mt-4 rounded-md border border-[var(--border-hairline)] p-4 text-sm">
            <p>
              Queued {sendResult.queuedCount} email{sendResult.queuedCount === 1 ? "" : "s"} for sending. Sending happens
              in the background — check back in a moment for results.
            </p>
            <button type="button" className={`${btnSecondary} mt-2`} onClick={checkResults} disabled={checkingResults}>
              {checkingResults ? "Checking…" : "Check results"}
            </button>
            {batchLogs && (
              <ul className="mt-3 space-y-1">
                {batchLogs.map((log) => (
                  <li key={log.id} className="flex items-center gap-2">
                    <StatusBadge status={log.status} />
                    <span>{log.customer_name ?? log.recipient_email}</span>
                    {log.status === "failed" && <span className="text-xs text-[var(--text-muted)]">{log.error_message}</span>}
                  </li>
                ))}
                {batchLogs.length < sendResult.queuedCount && (
                  <li className="text-xs text-[var(--text-muted)]">Still sending — check again shortly.</li>
                )}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sent Email (platform-wide log of every email ever sent -- bulk sends and
// individual document sends alike). Restores the "Sent Email" view that
// used to live in the old Email dropdown before Bulk Email and Email
// Templates were split out.
// ---------------------------------------------------------------------------

const LOG_PAGE_SIZE = 50;

function SentEmailTab() {
  const [page, setPage] = useState(1);
  const [templateFilter, setTemplateFilter] = useState<EmailTemplateKey | "">("");
  const [statusFilter, setStatusFilter] = useState("");

  const filterQuery = `${templateFilter ? `&template_key=${templateFilter}` : ""}${
    statusFilter ? `&status=${statusFilter}` : ""
  }`;
  const url = `/email-logs/?page_size=${LOG_PAGE_SIZE}&page=${page}${filterQuery}`;
  const { items, count, loading } = useApiList<EmailLog>(url);

  const totalPages = Math.max(1, Math.ceil(count / LOG_PAGE_SIZE));

  function formatDate(iso: string) {
    return new Date(iso).toLocaleString("en-ZA", { dateStyle: "medium", timeStyle: "short" });
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select
          className={filterSelectClass}
          value={templateFilter}
          onChange={(e) => { setTemplateFilter(e.target.value as EmailTemplateKey | ""); setPage(1); }}
        >
          <option value="">All templates</option>
          {Object.entries(ALL_TEMPLATE_LABELS).map(([key, label]) => (
            <option key={key} value={key}>{label}</option>
          ))}
        </select>
        <select className={filterSelectClass} value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}>
          <option value="">All statuses</option>
          <option value="sent">Sent</option>
          <option value="failed">Failed</option>
        </select>
        {(templateFilter || statusFilter) && (
          <button
            type="button"
            className={btnSecondary}
            onClick={() => { setTemplateFilter(""); setStatusFilter(""); setPage(1); }}
          >
            Clear filters
          </button>
        )}
      </div>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <>
          <Table>
            <THead>
              <tr>
                <TH>Recipient</TH>
                <TH>Template</TH>
                <TH>Subject</TH>
                <TH>Status</TH>
                <TH>Sent by</TH>
                <TH>Date</TH>
              </tr>
            </THead>
            <tbody>
              {items.map((log) => (
                <TR key={log.id}>
                  <TD>
                    <div className="font-medium">{log.customer_name ?? log.recipient_email}</div>
                    {log.customer_name && <div className="text-xs text-[var(--text-muted)]">{log.recipient_email}</div>}
                  </TD>
                  <TD>{log.template_name}</TD>
                  <TD className="max-w-xs truncate text-[var(--text-secondary)]">{log.subject || "—"}</TD>
                  <TD>
                    <StatusBadge status={log.status} />
                    {log.status === "failed" && log.error_message && (
                      <div className="mt-1 max-w-xs text-xs text-[var(--text-muted)]">{log.error_message}</div>
                    )}
                  </TD>
                  <TD>{log.sent_by_name ?? "—"}</TD>
                  <TD className="whitespace-nowrap text-[var(--text-secondary)]">{formatDate(log.created_at)}</TD>
                </TR>
              ))}
              {items.length === 0 && (
                <TR>
                  <TD className="text-[var(--text-muted)]">No emails sent yet.</TD>
                </TR>
              )}
            </tbody>
          </Table>

          <div className="mt-3 flex items-center justify-between text-sm text-[var(--text-muted)]">
            <span>
              Showing {items.length === 0 ? 0 : (page - 1) * LOG_PAGE_SIZE + 1}–{(page - 1) * LOG_PAGE_SIZE + items.length} of {count}
            </span>
            <div className="flex gap-2">
              <button type="button" className={btnSecondary} disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                Previous
              </button>
              <span className="px-2 py-2">Page {page} of {totalPages}</span>
              <button type="button" className={btnSecondary} disabled={page >= totalPages} onClick={() => setPage((p) => Math.min(totalPages, p + 1))}>
                Next
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
