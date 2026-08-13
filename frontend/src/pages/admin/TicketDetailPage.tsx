import { useEffect, useState, type FormEvent } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../../api/client";
import { PageHeader } from "../../components/PageHeader";
import { StatusBadge } from "../../components/StatusBadge";
import { inputClass, btnPrimary } from "../../components/Modal";
import type { Ticket } from "../../types";

export function TicketDetailPage() {
  const { id } = useParams();
  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [message, setMessage] = useState("");
  const [isInternal, setIsInternal] = useState(false);
  const [sending, setSending] = useState(false);

  function load() {
    if (!id) return;
    api.get<Ticket>(`/tickets/${id}/`).then((res) => setTicket(res.data));
  }
  useEffect(load, [id]);

  async function updateStatus(status: string) {
    if (!ticket) return;
    await api.patch(`/tickets/${ticket.id}/`, { status });
    load();
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!ticket || !message.trim()) return;
    setSending(true);
    try {
      await api.post("/ticket-comments/", { ticket: ticket.id, message, is_internal: isInternal });
      setMessage("");
      load();
    } finally {
      setSending(false);
    }
  }

  if (!ticket) return <p className="text-[var(--text-muted)]">Loading…</p>;

  return (
    <div className="mx-auto max-w-3xl">
      <Link to="/admin/tickets" className="mb-4 inline-block text-sm text-[var(--series-1)] hover:underline">
        ← Back to tickets
      </Link>
      <PageHeader
        title={`${ticket.ticket_number}: ${ticket.subject}`}
        subtitle={`${ticket.customer_name} · ${ticket.department} · Assigned to ${ticket.assigned_to_name ?? "Unassigned"}`}
        actions={
          <>
            <StatusBadge status={ticket.priority} />
            <select
              className="rounded-md border border-[var(--baseline)] px-2 py-1 text-sm"
              value={ticket.status}
              onChange={(e) => updateStatus(e.target.value)}
            >
              <option value="open">Open</option>
              <option value="pending">Pending Customer</option>
              <option value="resolved">Resolved</option>
              <option value="closed">Closed</option>
            </select>
          </>
        }
      />

      <div className="mb-6 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 text-sm">
        {ticket.description || <span className="text-[var(--text-muted)]">No description provided.</span>}
      </div>

      <h2 className="mb-2 text-sm font-semibold">Conversation</h2>
      <div className="mb-4 space-y-3">
        {ticket.comments.map((c) => (
          <div
            key={c.id}
            className={`rounded-lg border p-3 text-sm ${c.is_internal ? "border-[var(--status-warning)] bg-[#fff9ec]" : "border-[var(--border-hairline)] bg-[var(--surface-1)]"}`}
          >
            <div className="mb-1 flex items-center justify-between text-xs text-[var(--text-muted)]">
              <span className="font-medium text-[var(--text-secondary)]">{c.author_name ?? "System"}</span>
              <span>{new Date(c.created_at).toLocaleString()} {c.is_internal && "· internal note"}</span>
            </div>
            <p>{c.message}</p>
          </div>
        ))}
        {ticket.comments.length === 0 && <p className="text-sm text-[var(--text-muted)]">No comments yet.</p>}
      </div>

      <form onSubmit={handleSubmit} className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4">
        <textarea
          className={`${inputClass} mb-2`}
          rows={3}
          placeholder="Write a reply…"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
        />
        <div className="flex items-center justify-between">
          <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
            <input type="checkbox" checked={isInternal} onChange={(e) => setIsInternal(e.target.checked)} />
            Internal note (hidden from customer)
          </label>
          <button type="submit" disabled={sending} className={btnPrimary}>{sending ? "Sending…" : "Send reply"}</button>
        </div>
      </form>
    </div>
  );
}
