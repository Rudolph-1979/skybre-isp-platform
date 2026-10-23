import { useEffect, useState, type FormEvent } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { PageHeader } from "../../components/PageHeader";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { ActivityList } from "../../components/ActivityFeed";
import { TicketJobsPanel } from "../../components/TicketJobsPanel";
import { usePolling } from "../../hooks/usePolling";
import { formatIsoDateTime } from "../../utils/billingTerms";
import { apiErrorMessage } from "../../utils/apiError";
import type { Ticket, AuditEvent } from "../../types";

export function TicketDetailPage() {
  const { id } = useParams();
  const { user } = useAuth();
  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [message, setMessage] = useState("");
  const [isInternal, setIsInternal] = useState(false);
  const [sending, setSending] = useState(false);
  const [accepting, setAccepting] = useState(false);
  const [showEscalate, setShowEscalate] = useState(false);
  const [escalateReason, setEscalateReason] = useState("");
  const [escalating, setEscalating] = useState(false);
  const [decidingNote, setDecidingNote] = useState("");
  const [deciding, setDeciding] = useState(false);
  const [actionError, setActionError] = useState("");
  // Staff-only. The endpoint refuses customers outright (see
  // TicketViewSet.history) -- this page is admin-side, but the check that
  // matters is the server's, not the absence of a link here.
  const [history, setHistory] = useState<AuditEvent[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);

  // `quiet` is for the background poll: same fetch, but without raising
  // the history panel's loading flag, which would otherwise blank a
  // readable panel every fifteen seconds.
  //
  // Note what is NOT reloaded here: `message`. The reply someone is
  // halfway through typing lives in its own state and is never touched by
  // a refresh, which is the one thing a polling page must not get wrong.
  function load(quiet = false) {
    if (!id) return;
    api.get<Ticket>(`/tickets/${id}/`).then((res) => setTicket(res.data));
    // Reloaded alongside the ticket rather than once on mount: changing
    // the status or leaving a note is itself an audited event, and a
    // history panel that still shows the state before the thing you just
    // did reads as broken.
    if (!quiet) setHistoryLoading(true);
    api
      .get<{ results: AuditEvent[] }>(`/tickets/${id}/history/`)
      .then((res) => setHistory(res.data.results))
      .catch(() => setHistory([]))
      .finally(() => setHistoryLoading(false));
  }
  useEffect(load, [id]);

  // A customer replying in the Conecto app appends to this conversation
  // with nothing on this page to announce it. Faster than the list: an
  // agent reading a thread is the person most likely to be answered while
  // they are still looking at it.
  usePolling(() => load(true), 15_000);

  async function updateStatus(status: string) {
    if (!ticket) return;
    await api.patch(`/tickets/${ticket.id}/`, { status });
    load();
  }

  async function handleAccept() {
    if (!ticket) return;
    setActionError("");
    setAccepting(true);
    try {
      await api.post(`/tickets/${ticket.id}/accept/`);
      load();
    } catch (err) {
      setActionError(apiErrorMessage(err, "Could not accept this ticket."));
    } finally {
      setAccepting(false);
    }
  }

  async function handleEscalate(e: FormEvent) {
    e.preventDefault();
    if (!ticket) return;
    setActionError("");
    setEscalating(true);
    try {
      await api.post(`/tickets/${ticket.id}/escalate/`, { reason: escalateReason });
      setShowEscalate(false);
      setEscalateReason("");
      load();
    } catch (err) {
      setActionError(apiErrorMessage(err, "Could not escalate this ticket."));
    } finally {
      setEscalating(false);
    }
  }

  async function handleDecideEscalation(decision: "accepted" | "rejected") {
    if (!ticket) return;
    setActionError("");
    setDeciding(true);
    try {
      await api.post(`/tickets/${ticket.id}/decide-escalation/`, {
        decision,
        decision_note: decidingNote,
      });
      setDecidingNote("");
      load();
    } catch (err) {
      setActionError(apiErrorMessage(err, "Could not record this decision."));
    } finally {
      setDeciding(false);
    }
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

  const isAdmin = user?.role === "admin";
  const isManagement = user?.role === "management";
  const canEscalate = !!ticket.accepted_by && (ticket.accepted_by === user?.id || isAdmin) && !ticket.pending_escalation;
  const canDecide = !!ticket.pending_escalation && (user?.is_noc || isAdmin);
  const pastEscalations = ticket.escalations.filter((e) => e.decision !== "pending");
  // Rudolph, 26 Sep: "Only clients may resolve tickets" -- a full
  // workflow change. A plain staff member can no longer set a customer
  // ticket to Resolved directly (see TicketViewSet.perform_update); Admin
  // keeps the same override it already has elsewhere (escalate), and an
  // internal (job-feedback) ticket has no customer to hand that decision
  // to, so the receiving staff member still resolves (and closes) it
  // exactly as before.
  //
  // Rudolph, 30 Sep, split Closed out on its own terms: "only management
  // may close and clients may Resolve" -- Closed is a Management/Admin
  // housekeeping action (same bar as the KPI report), separate from the
  // client-only Resolved rule above.
  const canResolveDirectly = ticket.is_internal || isAdmin;
  const canCloseDirectly = ticket.is_internal || isAdmin || isManagement;
  // Message shared by the select's tooltip and the info banner below it --
  // null when this staff member already has both permissions and nothing
  // needs explaining.
  const resolveClosePolicyNote = canResolveDirectly && canCloseDirectly
    ? null
    : canCloseDirectly
    ? "Only the customer can mark this ticket Resolved. You can still close it, reopen it, or move it between Open and Pending Customer."
    : "Only the customer can mark this ticket Resolved, and only Management can mark it Closed. You can still reopen it, or move it between Open and Pending Customer.";
  const isCurrentlyResolved = ticket.status === "resolved" || ticket.status === "closed";

  return (
    <div className="mx-auto max-w-3xl">
      <Link to="/admin/tickets" className="mb-4 inline-block text-sm text-[var(--series-1)] hover:underline">
        ← Back to tickets
      </Link>
      <PageHeader
        title={`${ticket.ticket_number}: ${ticket.subject}`}
        subtitle={`${
          ticket.is_internal ? "Job feedback — staff only" : ticket.customer_name ?? "No customer"
        } · ${ticket.department} · Assigned to ${ticket.assigned_to_name ?? "Unassigned"}`}
        actions={
          <>
            <StatusBadge status={ticket.priority} />
            <select
              className="rounded-md border border-[var(--baseline)] px-2 py-1 text-sm"
              value={ticket.status}
              onChange={(e) => updateStatus(e.target.value)}
              title={resolveClosePolicyNote ?? undefined}
            >
              <option value="open">Open</option>
              <option value="pending">Pending Customer</option>
              {canResolveDirectly && <option value="resolved">Resolved</option>}
              {canCloseDirectly && <option value="closed">Closed</option>}
              {(
                // Not offered as a choice when this staff member lacks
                // that specific permission -- rendered only so the select
                // still shows the ticket's real state when it was someone
                // else (the customer, or Management) who put it there.
                (ticket.status === "resolved" && !canResolveDirectly) ||
                (ticket.status === "closed" && !canCloseDirectly)
              ) && (
                <option value={ticket.status}>{ticket.status === "resolved" ? "Resolved" : "Closed"}</option>
              )}
            </select>
          </>
        }
      />

      {actionError && (
        <p className="mb-4 rounded-md border border-[var(--status-critical)] bg-[#fbeaea] px-3 py-2 text-sm text-[var(--status-critical)]">
          {actionError}
        </p>
      )}

      {/* Acceptance / escalation: who owns this ticket, and the path to
          NOC when the owner decides it's theirs to decide, not support's.
          Feeds the yearly KPI report -- tickets_accepted counts the
          accept below, tickets_resolved_without_escalation checks whether
          any row ever appears here at all (see TicketViewSet.kpi_report). */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] px-3 py-2 text-sm">
        <span className="text-[var(--text-secondary)]">
          {ticket.accepted_by ? (
            <>Accepted by <span className="font-medium text-[var(--text-primary)]">{ticket.accepted_by_name}</span> · {formatIsoDateTime(ticket.accepted_at!)}</>
          ) : (
            <span className="text-[var(--text-muted)]">Not yet accepted by anyone.</span>
          )}
        </span>
        <div className="flex gap-2">
          {!ticket.accepted_by && (
            <button type="button" className={btnPrimary} disabled={accepting} onClick={handleAccept}>
              {accepting ? "Accepting…" : "Accept ticket"}
            </button>
          )}
          {canEscalate && (
            <button
              type="button"
              className={btnSecondary}
              onClick={() => {
                setEscalateReason("");
                setActionError("");
                setShowEscalate(true);
              }}
            >
              Escalate to NOC
            </button>
          )}
        </div>
      </div>

      {/* Book a job from this ticket (8 Oct 2026). */}
      <TicketJobsPanel ticketId={ticket.id} />

      {ticket.disqualified_from_kpi && (
        <p className="mb-4 rounded-md border border-[var(--border-hairline)] bg-[var(--tint-subtle)] px-3 py-2 text-xs text-[var(--text-secondary)]">
          This ticket was reopened after being resolved once, so it no longer counts toward anyone's yearly KPI
          report — even once it's resolved again.
        </p>
      )}

      {ticket.pending_escalation && (
        <div className="mb-4 rounded-lg border border-[var(--status-warning)] bg-[var(--tint-warning)] p-3 text-sm">
          <div className="mb-1 flex items-center justify-between">
            <span className="font-medium text-[var(--text-primary)]">Escalated to NOC</span>
            <StatusBadge status="pending" />
          </div>
          <p className="text-xs text-[var(--text-muted)]">
            By {ticket.pending_escalation.escalated_by_name ?? "—"} · {formatIsoDateTime(ticket.pending_escalation.escalated_at)}
          </p>
          {ticket.pending_escalation.reason && (
            <p className="mt-2 text-[var(--text-secondary)]">{ticket.pending_escalation.reason}</p>
          )}
          {canDecide && (
            <div className="mt-3 border-t border-[var(--status-warning)] pt-3">
              <FormField label="Decision note (optional)">
                <input
                  className={inputClass}
                  value={decidingNote}
                  onChange={(e) => setDecidingNote(e.target.value)}
                  placeholder="Why it's NOC's, or why it's solvable by support…"
                />
              </FormField>
              <div className="flex gap-2">
                <button
                  type="button"
                  className={btnPrimary}
                  disabled={deciding}
                  onClick={() => handleDecideEscalation("accepted")}
                >
                  {deciding ? "Saving…" : "Accept — this is NOC's"}
                </button>
                <button
                  type="button"
                  className={btnSecondary}
                  disabled={deciding}
                  onClick={() => handleDecideEscalation("rejected")}
                >
                  {deciding ? "Saving…" : "Reject — solvable by support"}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Said before the thread, not after it. Somebody about to type
          knows who reads it, and on this kind of ticket the answer is not
          the one they are used to. */}
      {ticket.is_internal && (
        <p className="mb-4 rounded-md border border-[var(--status-warning)] bg-[var(--tint-warning)] px-3 py-2 text-xs text-[var(--text-secondary)]">
          Raised by a technician from a job in the Conecto app. No customer can see this ticket or
          any reply on it — replies go to the technician, under your name.
        </p>
      )}

      {resolveClosePolicyNote && !isCurrentlyResolved && (
        <p className="mb-4 rounded-md border border-[var(--border-hairline)] bg-[var(--tint-subtle)] px-3 py-2 text-xs text-[var(--text-secondary)]">
          {resolveClosePolicyNote}
        </p>
      )}

      <div className="mb-6 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 text-sm">
        {ticket.description || <span className="text-[var(--text-muted)]">No description provided.</span>}
      </div>

      <h2 className="mb-2 text-sm font-semibold">Conversation</h2>
      <div className="mb-4 space-y-3">
        {ticket.comments.map((c) => (
          <div
            key={c.id}
            className={`rounded-lg border p-3 text-sm ${c.is_internal ? "border-[var(--status-warning)] bg-[var(--tint-warning)]" : "border-[var(--border-hairline)] bg-[var(--surface-1)]"}`}
          >
            <div className="mb-1 flex items-center justify-between text-xs text-[var(--text-muted)]">
              <span className="font-medium text-[var(--text-secondary)]">{c.author_name ?? "System"}</span>
              <span>{formatIsoDateTime(c.created_at)} {c.is_internal && "· internal note"}</span>
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
            {/* On a job-feedback ticket the customer is not in the
                conversation at all, so "hidden from customer" would be
                describing the wrong audience. Here it hides the note from
                the TECHNICIAN, which is a different decision and has to
                read like one. */}
            {ticket.is_internal
              ? "Private note (not sent to the technician)"
              : "Internal note (hidden from customer)"}
          </label>
          <button type="submit" disabled={sending} className={btnPrimary}>{sending ? "Sending…" : "Send reply"}</button>
        </div>
      </form>

      {pastEscalations.length > 0 && (
        <>
          <h2 className="mb-2 mt-6 text-sm font-semibold">Escalation history</h2>
          <div className="space-y-2">
            {pastEscalations.map((e) => (
              <div key={e.id} className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-3 text-sm">
                <div className="mb-1 flex items-center justify-between">
                  <span>
                    Escalated by <span className="font-medium">{e.escalated_by_name ?? "—"}</span> · {formatIsoDateTime(e.escalated_at)}
                  </span>
                  <StatusBadge status={e.decision} />
                </div>
                {e.reason && <p className="text-[var(--text-secondary)]">{e.reason}</p>}
                {e.decided_by_name && e.decided_at && (
                  <p className="mt-1 text-xs text-[var(--text-muted)]">
                    Decided by {e.decided_by_name} · {formatIsoDateTime(e.decided_at)}
                    {e.decision_note && ` — ${e.decision_note}`}
                  </p>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      <h2 className="mb-2 mt-6 text-sm font-semibold">History</h2>
      <p className="mb-2 text-xs text-[var(--text-muted)]">
        Who changed what on this ticket, including comments that were edited or deleted. Staff only — never shown
        in the customer portal.
      </p>
      <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)]">
        {/* showTarget off: every row here is this ticket, so repeating its
            number on each one is noise. */}
        <ActivityList
          events={history}
          loading={historyLoading}
          emptyMessage="Nothing recorded against this ticket yet."
          showTarget={false}
        />
      </div>

      {showEscalate && (
        <Modal title="Escalate to NOC" onClose={() => setShowEscalate(false)}>
          <form onSubmit={handleEscalate}>
            <p className="mb-3 text-sm text-[var(--text-secondary)]">
              NOC will see this ticket as awaiting a decision — either they take it on, or they reject it back to
              you as solvable by support.
            </p>
            <FormField label="Reason (optional)">
              <textarea
                className={inputClass}
                rows={3}
                value={escalateReason}
                onChange={(e) => setEscalateReason(e.target.value)}
                placeholder="What makes this NOC's?"
              />
            </FormField>
            {actionError && <p className="mb-3 text-sm text-[var(--status-critical)]">{actionError}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowEscalate(false)}>
                Cancel
              </button>
              <button type="submit" className={btnPrimary} disabled={escalating}>
                {escalating ? "Escalating…" : "Escalate"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
