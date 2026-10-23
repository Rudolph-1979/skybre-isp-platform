import { useEffect, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { useViewAs } from "../../context/ViewAsContext";
import { PageHeader } from "../../components/PageHeader";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import type { Ticket } from "../../types";

export function PortalTickets() {
  // The signed-in customer normally, or the customer a staff member is
  // viewing the portal as (see ViewAsContext).
  const { effectiveCustomerId: customerId, target: viewingAs } = useViewAs();
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ subject: "", description: "", department: "support", priority: "medium" });
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);

  function load() {
    if (!customerId) return;
    api.get<{ results: Ticket[] }>(`/tickets/?customer=${customerId}&ordering=-created_at`).then((res) => setTickets(res.data.results));
  }
  useEffect(load, [customerId]);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await api.post("/tickets/", form);
      setShowModal(false);
      setForm({ subject: "", description: "", department: "support", priority: "medium" });
      load();
    } finally {
      setSaving(false);
    }
  }

  async function handleReply(ticketId: number) {
    if (!reply.trim()) return;
    setSending(true);
    try {
      await api.post("/ticket-comments/", { ticket: ticketId, message: reply });
      setReply("");
      load();
    } finally {
      setSending(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Support Tickets"
        subtitle="Get help from our support team."
        actions={
          viewingAs ? undefined : (
            <button className={btnPrimary} onClick={() => setShowModal(true)}>+ New ticket</button>
          )
        }
      />

      <div className="space-y-3">
        {tickets.map((t) => (
          <div key={t.id} className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4">
            <div className="flex cursor-pointer items-center justify-between" onClick={() => setExpanded(expanded === t.id ? null : t.id)}>
              <div>
                <p className="font-medium">{t.ticket_number}: {t.subject}</p>
                <p className="text-xs text-[var(--text-muted)]">{t.department} · {new Date(t.created_at).toLocaleDateString()}</p>
              </div>
              <div className="flex items-center gap-2">
                <StatusBadge status={t.priority} />
                <StatusBadge status={t.status} />
              </div>
            </div>
            {expanded === t.id && (
              <div className="mt-4 space-y-2 border-t border-[var(--border-hairline)] pt-4">
                <p className="text-sm text-[var(--text-secondary)]">{t.description}</p>
                {t.comments.map((c) => (
                  <div key={c.id} className="rounded-md bg-[var(--tint-subtle)] p-2 text-sm">
                    <p className="text-xs text-[var(--text-muted)]">{c.author_name ?? "Support"} · {new Date(c.created_at).toLocaleString()}</p>
                    <p>{c.message}</p>
                  </div>
                ))}
                {viewingAs ? (
                  <p className="pt-2 text-xs text-[var(--text-muted)]">
                    Replying is disabled in customer view — a reply sent here would be recorded as
                    yours, on the customer's ticket. Answer it from Tickets in the admin area instead.
                  </p>
                ) : (
                  <div className="flex gap-2 pt-2">
                    <input
                      className={inputClass}
                      placeholder="Type a reply…"
                      value={reply}
                      onChange={(e) => setReply(e.target.value)}
                    />
                    <button className={btnPrimary} disabled={sending} onClick={() => handleReply(t.id)}>
                      {sending ? "…" : "Send"}
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
        {tickets.length === 0 && <p className="text-sm text-[var(--text-muted)]">You have no support tickets.</p>}
      </div>

      {showModal && (
        <Modal title="New support ticket" onClose={() => setShowModal(false)}>
          <form onSubmit={handleCreate}>
            <FormField label="Subject">
              <input className={inputClass} required value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} />
            </FormField>
            <FormField label="Describe the issue">
              <textarea className={inputClass} rows={4} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </FormField>
            <FormField label="Department">
              <select className={inputClass} value={form.department} onChange={(e) => setForm({ ...form, department: e.target.value })}>
                <option value="support">Technical Support</option>
                <option value="billing">Billing</option>
                <option value="sales">Sales</option>
              </select>
            </FormField>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>Cancel</button>
              <button type="submit" disabled={saving} className={btnPrimary}>{saving ? "Submitting…" : "Submit ticket"}</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
