import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { PageHeader } from "../../components/PageHeader";
import { StatCard } from "../../components/StatCard";
import { StatusBadge } from "../../components/StatusBadge";
import { SearchableSelect } from "../../components/SearchableSelect";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { btnPrimary } from "../../components/Modal";
import { useAuth } from "../../context/AuthContext";
import { usePolling } from "../../hooks/usePolling";
import { canAccessSection } from "../../utils/permissions";
import { formatIsoDate } from "../../utils/billingTerms";

/**
 * The Support page (#212, 22 Oct 2026 -- Rudolph approved the mockup: "Ok
 * build it please"). One request, GET /support/dashboard/, fills it; see
 * backend tickets/support.py for what every figure means. A panel comes back
 * as null when this person doesn't hold the section it is built from, and is
 * left out here.
 */

type Period = "today" | "week" | "month";

type QueueRow = {
  id: number;
  number: string;
  subject: string;
  department_label: string;
  priority: string;
  status: string;
  customer: number;
  customer_ref: string;
  customer_name: string;
  assigned_to: number | null;
  assigned_name: string;
  mine: boolean;
  waiting_state: "waiting" | "answered" | "on_client";
  waiting_seconds: number;
  waiting_long: boolean;
  line: string | null;
};

type ListItem = { customer?: number; customer_ref?: string; ticket?: number; number?: string; name: string; detail: string };
type WaitingItem = { key: string; label: string; sub: string; count: number; link: string; items: ListItem[] };

type Outage = { label: string; source: string; count: number; first_down: string; last_down: string; customers: number[] };

type StaffRow = {
  user: number | null;
  name: string;
  is_me: boolean;
  tickets: number;
  first_reply_median: number | null;
  within: number;
  decided: number;
  within_pct: number | null;
  resolved: number;
};

type SupportDashboard = {
  today: string;
  next_run_date: string;
  run_time: string;
  settings: { reply_target_minutes: number; office_start: string; office_end: string; office_days: number[] };
  tiles: {
    open_tickets: { count: number; waiting_long: number };
    offline: { count: number; in_outage: number } | null;
    cutoffs: { count: number; extension_dates: number } | null;
    extensions: number | null;
    pending: number | null;
    paused: { count: number; resuming_this_week: number } | null;
  };
  queue: { counts: { all: number; mine: number; unassigned: number; urgent: number; waiting_long: number }; results: QueueRow[] };
  response: {
    tickets: number;
    client_raised: number;
    logged_by_staff: number;
    first_reply_median: number | null;
    replied: number;
    within: number;
    decided: number;
    within_pct: number | null;
    resolved: number;
    resolve_median: number | null;
    reopened: number;
    target_minutes: number;
    oldest_waiting: {
      id: number; number: string; customer_name: string; priority: string; assigned_name: string;
      office_minutes: number; seconds: number;
    } | null;
    chart: { label: string; within: number; decided: number; pct: number | null }[];
    staff: StaffRow[];
    staff_scope: "all" | "own";
  };
  offline: {
    count: number;
    outages: Outage[];
    results: {
      customer: number; customer_ref: string; name: string; username: string; network_point: string;
      last_seen: string; offline_seconds: number; reason: string; open_ticket: boolean; in_outage: boolean;
    }[];
  } | null;
  cutoffs: {
    run_date: string; run_time: string; auto_suspend_enabled: boolean; count: number; extension_dates: number;
    results: {
      customer: number; customer_ref: string; name: string; balance: string; oldest_invoice_due: string | null;
      reminders_sent: number; extension_date: boolean;
    }[];
  } | null;
  waiting: WaitingItem[];
  visits: {
    id: number; start: string; title: string; job_type_label: string; status: string; status_label: string; urgent: boolean;
    customer: number | null; customer_name: string; technicians: string[]; vehicles: string[];
  }[] | null;
};

// Where a "within target" percentage turns from orange to blue. A display
// rule, not a setting: the target itself (minutes) is in Configs -> Support.
const GOAL_PCT = 80;
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const CARD = "min-w-0 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm";
const LINK = "text-sm font-medium text-[var(--series-1)] hover:underline";

/** Office minutes, e.g. 38 min, 2h 05m, 3d 4h (8-hour office days not assumed). */
function fmtMinutes(m: number | null | undefined) {
  if (m === null || m === undefined) return "—";
  if (m < 60) return `${Math.round(m)} min`;
  const h = Math.floor(m / 60);
  const rest = Math.round(m - h * 60);
  return rest ? `${h}h ${String(rest).padStart(2, "0")}m` : `${h}h`;
}

/** Wall-clock seconds, e.g. 40m, 3h 10m, 2d 4h. */
function fmtAgo(s: number) {
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${String(m % 60).padStart(2, "0")}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

function clock(iso: string) {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function longDate(iso: string) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-ZA", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}

function runLabel(runDate: string, today: string, runTime: string) {
  if (runDate === today) return `today ${runTime}`;
  return `${new Date(`${runDate}T00:00:00`).toLocaleDateString("en-ZA", { weekday: "long" })} ${runTime}`;
}

function pill(on: boolean) {
  return `rounded-full border px-3 py-1.5 text-sm font-medium transition-colors ${
    on
      ? "border-[var(--series-1)] bg-[var(--series-1)] text-white"
      : "border-[var(--baseline)] text-[var(--text-secondary)] hover:bg-[var(--tint-hover)]"
  }`;
}

export function SupportPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [period, setPeriod] = useState<Period>("week");
  const [data, setData] = useState<SupportDashboard | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<"all" | "mine" | "unassigned" | "urgent">("all");
  const [open, setOpen] = useState<string | null>(null);
  const [clients, setClients] = useState<{ id: number; full_name: string; customer_id: string; company_name?: string }[]>([]);
  const canCustomers = canAccessSection(user, "customers");

  function load(quiet = false) {
    api
      .get<SupportDashboard>(`/support/dashboard/?period=${period}`)
      .then((r) => {
        setData(r.data);
        setError("");
      })
      .catch(() => {
        if (!quiet) setError("Could not load the support page.");
      });
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => load(false), [period]);
  usePolling(() => load(true), 60_000);

  useEffect(() => {
    if (!canCustomers) return;
    api
      .get<{ results: { id: number; full_name: string; customer_id: string; company_name?: string }[] }>("/customers/picker/")
      .then((r) => setClients(r.data.results))
      .catch(() => setClients([]));
  }, [canCustomers]);

  if (error && !data) return <p className="text-[var(--status-critical)]">{error}</p>;
  if (!data) return <p className="text-[var(--text-muted)]">Loading support…</p>;

  const { tiles, queue, response: resp, offline, cutoffs, waiting, visits, settings } = data;
  const rows = queue.results.filter((r) =>
    filter === "all" ? true
      : filter === "mine" ? r.mine
      : filter === "unassigned" ? !r.assigned_to
      : r.priority === "urgent" || r.priority === "high"
  );
  const officeDays = settings.office_days.map((d) => DAYS[d]).join(", ");

  return (
    <div className="space-y-6">
      <PageHeader
        title="Support"
        subtitle={`${longDate(data.today)} · next billing run ${runLabel(data.next_run_date, data.today, data.run_time)}`}
        actions={
          <>
            {canCustomers && (
              <div className="w-72 max-w-full">
                <SearchableSelect
                  options={clients.map((c) => ({
                    value: String(c.id),
                    label: c.full_name,
                    meta: c.customer_id,
                    searchText: `${c.full_name} ${c.company_name ?? ""} ${c.customer_id}`,
                  }))}
                  value=""
                  onChange={(v) => v && navigate(`/admin/customers/${v}`)}
                  placeholder="Find a client…"
                />
              </div>
            )}
            <button type="button" className={btnPrimary} onClick={() => navigate("/admin/tickets?new=1")}>
              + New ticket
            </button>
          </>
        }
      />

      {/* Tiles */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatCard
          label="Open tickets"
          value={tiles.open_tickets.count}
          sublabel={tiles.open_tickets.waiting_long ? `${tiles.open_tickets.waiting_long} waiting over 12 hours` : "none waiting over 12 hours"}
          accent={tiles.open_tickets.waiting_long ? "status-critical" : "series-1"}
          onClick={() => navigate("/admin/tickets?status=open")}
          actionLabel="Open tickets"
        />
        {tiles.offline && (
          <StatCard
            label="Recently offline"
            value={tiles.offline.count}
            sublabel={tiles.offline.in_outage ? `${tiles.offline.in_outage} in a possible outage` : "online in the last 24h, down now"}
            accent={tiles.offline.count ? "status-critical" : "status-good"}
            onClick={() => navigate("/admin/offline-customers")}
            actionLabel="Show the list"
          />
        )}
        {tiles.cutoffs && (
          <StatCard
            label={`Cut-off ${runLabel(data.next_run_date, data.today, data.run_time)}`}
            value={tiles.cutoffs.count}
            sublabel={tiles.cutoffs.extension_dates ? `${tiles.cutoffs.extension_dates} on an agreed extension date` : "expect calls after the run"}
            accent={tiles.cutoffs.count ? "status-warning" : "status-good"}
            onClick={canAccessSection(user, "finance") ? () => navigate("/admin?panel=blocks") : undefined}
            actionLabel={canAccessSection(user, "finance") ? "Show the list" : undefined}
          />
        )}
        {tiles.extensions !== null && (
          <StatCard label="Extension requests" value={tiles.extensions} sublabel="waiting for Management" accent="series-3" />
        )}
        {tiles.pending !== null && (
          <StatCard
            label="Pending activations"
            value={tiles.pending}
            sublabel="line added, not switched on"
            accent="series-3"
            onClick={canAccessSection(user, "services") ? () => navigate("/admin/services?status=pending") : undefined}
            actionLabel={canAccessSection(user, "services") ? "Show the lines" : undefined}
          />
        )}
        {tiles.paused && (
          <StatCard
            label="Paused lines"
            value={tiles.paused.count}
            sublabel={tiles.paused.resuming_this_week ? `${tiles.paused.resuming_this_week} back on this week` : "none back on this week"}
            accent="series-3"
            onClick={canAccessSection(user, "services") ? () => navigate("/admin/services?status=paused") : undefined}
            actionLabel={canAccessSection(user, "services") ? "Show the lines" : undefined}
          />
        )}
      </div>

      {/* Possible outages */}
      {offline?.outages.map((o) => (
        <div
          key={o.label}
          role="alert"
          className="flex flex-col gap-3 rounded-lg border border-[var(--status-warning)] bg-[var(--tint-warning)] p-4 sm:flex-row sm:items-center sm:justify-between"
        >
          <div>
            <p className="font-semibold text-[var(--text-primary)]">Possible outage: {o.label}</p>
            <p className="mt-1 text-sm text-[var(--text-secondary)]">
              {o.count} clients on this {o.source} went offline between {clock(o.first_down)} and {clock(o.last_down)}.
              Calls about no internet from these clients are probably this.
            </p>
          </div>
          <Link to="/admin/offline-customers" className={LINK}>See who is offline →</Link>
        </div>
      ))}

      {/* Response to clients */}
      <section className={CARD} aria-labelledby="resp-h">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 id="resp-h" className="text-base font-semibold text-[var(--text-primary)]">Response to clients</h2>
            <p className="mt-1 max-w-3xl text-xs text-[var(--text-muted)]">
              First reply = time from a client's ticket until a staff member first answers in a way the client can see
              (internal notes don't count). Target {fmtMinutes(settings.reply_target_minutes)}. Office time only:{" "}
              {settings.office_start}–{settings.office_end}, {officeDays}, public holidays skipped.
              {resp.logged_by_staff > 0 &&
                ` ${resp.logged_by_staff} ticket${resp.logged_by_staff === 1 ? "" : "s"} logged by staff (e.g. from a phone call) not counted for first reply.`}
            </p>
          </div>
          <div role="group" aria-label="Period" className="flex flex-wrap gap-1.5">
            {(["today", "week", "month"] as Period[]).map((p) => (
              <button key={p} type="button" aria-pressed={period === p} className={pill(period === p)} onClick={() => setPeriod(p)}>
                {p === "today" ? "Today" : p === "week" ? "Last 7 days" : "Last 30 days"}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Figure label="First reply (median)" value={fmtMinutes(resp.first_reply_median)} sub={`${resp.client_raised} client tickets`} />
          <Figure
            label="Replied within target"
            value={resp.within_pct === null ? "—" : `${resp.within_pct}%`}
            sub={`${resp.within} of ${resp.decided}`}
            tone={resp.within_pct === null ? undefined : resp.within_pct >= GOAL_PCT ? "good" : "warn"}
          />
          <Figure
            label="Time to resolve (median)"
            value={fmtMinutes(resp.resolve_median)}
            sub={`${resp.resolved} resolved${resp.reopened ? ` · ${resp.reopened} re-opened` : ""}`}
          />
          <Figure
            label="Oldest waiting for a reply"
            value={resp.oldest_waiting ? fmtMinutes(resp.oldest_waiting.office_minutes) : "None"}
            sub={
              resp.oldest_waiting
                ? `${resp.oldest_waiting.number} · ${resp.oldest_waiting.customer_name} · ${resp.oldest_waiting.assigned_name || "unassigned"}`
                : "every client ticket has a reply"
            }
            tone={resp.oldest_waiting && resp.oldest_waiting.office_minutes > resp.target_minutes ? "bad" : undefined}
            to={resp.oldest_waiting ? `/admin/tickets/${resp.oldest_waiting.id}` : undefined}
          />
        </div>

        <div className="mt-5 flex flex-col gap-6 lg:flex-row">
          <div className="min-w-0 flex-1">
            <p className="mb-2 text-sm font-medium text-[var(--text-secondary)]">
              Replied within target, by {period === "today" ? "hour" : period === "week" ? "day" : "week"}
            </p>
            <div className="flex h-40 items-end gap-2 border-b border-[var(--gridline)] pt-4">
              {resp.chart.map((b) => {
                const pct = b.pct ?? 0;
                const met = b.pct !== null && b.pct >= GOAL_PCT;
                return (
                  <div key={b.label} className="flex h-full min-w-0 flex-1 flex-col items-center justify-end gap-1">
                    <span className="text-[11px] font-medium tabular-nums text-[var(--text-secondary)]">
                      {b.pct === null ? "–" : `${b.pct}%`}
                    </span>
                    <div
                      title={`${b.label}: ${b.within} of ${b.decided} within target`}
                      className="w-full max-w-[44px] rounded-t"
                      style={{
                        height: `${b.pct === null ? 2 : Math.max(3, pct)}%`,
                        background: b.pct === null ? "var(--gridline)" : met ? "var(--series-1)" : "var(--series-2)",
                      }}
                    />
                  </div>
                );
              })}
            </div>
            <div className="mt-1 flex gap-2">
              {resp.chart.map((b) => (
                <span key={b.label} className="min-w-0 flex-1 truncate text-center text-[11px] text-[var(--text-muted)]">{b.label}</span>
              ))}
            </div>
            <p className="mt-2 text-xs text-[var(--text-muted)]">
              <span className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-[var(--series-1)] align-middle" /> {GOAL_PCT}% or more
              <span className="ml-4 mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-[var(--series-2)] align-middle" /> below {GOAL_PCT}%
              <span className="ml-4">– no client tickets decided yet</span>
            </p>
            <table className="sr-only">
              <caption>Replied within target</caption>
              <tbody>
                {resp.chart.map((b) => (
                  <tr key={b.label}><th>{b.label}</th><td>{b.pct === null ? "no data" : `${b.pct}% (${b.within} of ${b.decided})`}</td></tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="min-w-0 flex-[1.4]">
            <p className="mb-2 text-sm font-medium text-[var(--text-secondary)]">
              By staff member{resp.staff_scope === "own" ? " (your own row; the figures above are the whole team)" : ""}
            </p>
            {resp.staff.length === 0 ? (
              <p className="text-sm text-[var(--text-muted)]">No client tickets in this period.</p>
            ) : (
              <Table>
                <THead>
                  <tr><TH>Staff</TH><TH>Tickets</TH><TH>First reply</TH><TH>Within target</TH><TH>Resolved</TH></tr>
                </THead>
                <tbody>
                  {resp.staff.map((s) => (
                    <TR key={s.user ?? "none"}>
                      <TD className="font-medium">{s.name}{s.is_me ? " (you)" : ""}</TD>
                      <TD className="tabular-nums">{s.tickets}</TD>
                      <TD className="tabular-nums">{fmtMinutes(s.first_reply_median)}</TD>
                      <TD className="tabular-nums">
                        {s.within_pct === null ? "—" : (
                          <span className={s.within_pct >= GOAL_PCT ? "text-[var(--status-good)]" : "text-[var(--series-2)]"}>
                            {s.within_pct}% <span className="text-[var(--text-muted)]">({s.within}/{s.decided})</span>
                          </span>
                        )}
                      </TD>
                      <TD className="tabular-nums">{s.resolved}</TD>
                    </TR>
                  ))}
                </tbody>
              </Table>
            )}
            <p className="mt-2 text-xs text-[var(--text-muted)]">Counted against who each ticket is assigned to now.</p>
          </div>
        </div>
      </section>

      {/* Queue + offline */}
      <div className="flex flex-col gap-6 xl:flex-row xl:items-start">
        <section className={`${CARD} min-w-0 xl:flex-[3]`} aria-labelledby="queue-h">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 id="queue-h" className="text-base font-semibold text-[var(--text-primary)]">Ticket queue</h2>
            <div role="group" aria-label="Filter tickets" className="flex flex-wrap gap-1.5">
              {([
                ["all", `All open · ${queue.counts.all}`],
                ["mine", `Mine · ${queue.counts.mine}`],
                ["unassigned", `Unassigned · ${queue.counts.unassigned}`],
                ["urgent", `Urgent & high · ${queue.counts.urgent}`],
              ] as const).map(([k, label]) => (
                <button key={k} type="button" aria-pressed={filter === k} className={pill(filter === k)} onClick={() => setFilter(k)}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          {rows.length === 0 ? (
            <p className="py-6 text-center text-sm text-[var(--text-muted)]">Nothing here.</p>
          ) : (
            <Table>
              <THead>
                <tr><TH>Priority</TH><TH>Client</TH><TH>Subject</TH><TH>Waiting</TH><TH>Assigned</TH><TH>Line</TH></tr>
              </THead>
              <tbody>
                {rows.map((r) => (
                  <TR key={r.id} onClick={() => navigate(`/admin/tickets/${r.id}`)}>
                    <TD><StatusBadge status={r.priority} /></TD>
                    <TD>
                      <Link to={`/admin/customers/${r.customer}`} className="font-medium text-[var(--series-1)] hover:underline" onClick={(e) => e.stopPropagation()}>
                        {r.customer_name}
                      </Link>
                      <div className="text-xs text-[var(--text-muted)]">{r.customer_ref}</div>
                    </TD>
                    <TD className="min-w-[200px]">
                      {r.subject}
                      <div className="text-xs text-[var(--text-muted)]">{r.number} · {r.department_label}</div>
                    </TD>
                    <TD className="whitespace-nowrap">
                      {r.waiting_state === "waiting" ? (
                        <span className={`font-medium tabular-nums ${r.waiting_long ? "text-[var(--status-critical)]" : ""}`}>
                          {fmtAgo(r.waiting_seconds)}
                        </span>
                      ) : (
                        <span className="text-[var(--text-muted)]">
                          {r.waiting_state === "answered" ? `Answered ${fmtAgo(r.waiting_seconds)} ago` : "On client"}
                        </span>
                      )}
                    </TD>
                    <TD className="text-[var(--text-secondary)]">{r.assigned_name || "Unassigned"}</TD>
                    <TD>{r.line ? <StatusBadge status={r.line} /> : <span className="text-[var(--text-muted)]">—</span>}</TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          )}
          <p className="mt-2 text-xs text-[var(--text-muted)]">
            Waiting = time since the client's last message with no visible reply from us. Red after 12 hours.{" "}
            <Link to="/admin/tickets" className={LINK}>All tickets →</Link>
          </p>
        </section>

        {offline && (
          <section className={`${CARD} min-w-0 xl:flex-[2]`} aria-labelledby="offline-h">
            <div className="mb-2 flex items-baseline justify-between gap-2">
              <h2 id="offline-h" className="text-base font-semibold text-[var(--text-primary)]">Recently offline</h2>
              <span className="text-xs text-[var(--text-muted)]">online in the last 24h, down now</span>
            </div>
            {offline.results.length === 0 ? (
              <p className="py-6 text-center text-sm text-[var(--text-muted)]">Nobody — every line seen today is up.</p>
            ) : (
              <ul className="divide-y divide-[var(--border-hairline)]">
                {offline.results.map((o) => (
                  <li key={o.username} className="flex items-center justify-between gap-3 py-2.5">
                    <div className="min-w-0">
                      <Link to={`/admin/customers/${o.customer}`} className="font-medium text-[var(--series-1)] hover:underline">{o.name}</Link>
                      <p className="truncate text-xs text-[var(--text-muted)]" title={o.reason}>
                        {o.username}{o.network_point ? ` · ${o.network_point}` : ""}
                      </p>
                    </div>
                    <div className="shrink-0 text-right">
                      <p className="text-sm font-medium tabular-nums text-[var(--status-critical)]">down {fmtAgo(o.offline_seconds)}</p>
                      <p className="text-xs text-[var(--text-muted)]">
                        {o.in_outage ? "possible outage" : o.open_ticket ? "ticket open" : "no ticket yet"}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
            <Link to="/admin/offline-customers" className={`${LINK} mt-2 inline-block`}>
              All {offline.count} recently offline →
            </Link>
          </section>
        )}
      </div>

      {/* Cut-offs, waiting on support, visits */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2 xl:grid-cols-3">
        {cutoffs && (
          <section className={CARD} aria-labelledby="cut-h">
            <div className="mb-2 flex items-baseline justify-between gap-2">
              <h2 id="cut-h" className="text-base font-semibold text-[var(--text-primary)]">
                Cut-off {runLabel(cutoffs.run_date, data.today, cutoffs.run_time)}
              </h2>
              <span className="text-xs text-[var(--text-muted)]">expect calls after the run</span>
            </div>
            {!cutoffs.auto_suspend_enabled && (
              <p className="mb-2 text-xs text-[var(--text-muted)]">Auto-suspension is switched off, so nobody is cut automatically.</p>
            )}
            {cutoffs.results.length === 0 ? (
              <p className="py-6 text-center text-sm text-[var(--text-muted)]">Nobody is due for a cut-off.</p>
            ) : (
              <ul className="divide-y divide-[var(--border-hairline)]">
                {cutoffs.results.slice(0, 10).map((c) => (
                  <li key={c.customer} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 py-2.5">
                    <div className="min-w-0">
                      <Link to={`/admin/customers/${c.customer}`} className="font-medium text-[var(--series-1)] hover:underline">{c.name}</Link>
                      <p className="text-xs text-[var(--text-muted)]">
                        {c.extension_date
                          ? "Agreed extension ends"
                          : `${c.reminders_sent} reminder${c.reminders_sent === 1 ? "" : "s"} sent`}
                        {c.oldest_invoice_due ? ` · due ${formatIsoDate(c.oldest_invoice_due)}` : ""}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      {c.extension_date && <StatusBadge status="paused" label="Extension date" />}
                      <span className="font-semibold tabular-nums">R {Number(c.balance).toFixed(2)}</span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
            {canAccessSection(user, "finance") && (
              <Link to="/admin?panel=blocks" className={`${LINK} mt-2 inline-block`}>All upcoming cut-offs →</Link>
            )}
          </section>
        )}

        <section className={CARD} aria-labelledby="todo-h">
          <h2 id="todo-h" className="mb-2 text-base font-semibold text-[var(--text-primary)]">Waiting on support</h2>
          <ul className="space-y-2">
            {waiting.map((w) => (
              <li key={w.key} className="rounded-md border border-[var(--border-hairline)]">
                <button
                  type="button"
                  aria-expanded={open === w.key}
                  disabled={w.count === 0}
                  onClick={() => setOpen(open === w.key ? null : w.key)}
                  className="flex min-h-[44px] w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-[var(--tint-hover)] disabled:cursor-default disabled:hover:bg-transparent"
                >
                  <span>
                    <span className="block text-sm font-medium text-[var(--text-primary)]">{w.label}</span>
                    <span className="block text-xs text-[var(--text-muted)]">{w.sub}</span>
                  </span>
                  <span className={`text-lg font-semibold tabular-nums ${w.count ? "text-[var(--text-primary)]" : "text-[var(--text-muted)]"}`}>{w.count}</span>
                </button>
                {open === w.key && w.items.length > 0 && (
                  <ul className="border-t border-[var(--border-hairline)] px-3 py-2 text-sm">
                    {w.items.map((it, i) => (
                      <li key={i} className="py-1">
                        <Link
                          to={it.ticket ? `/admin/tickets/${it.ticket}` : `/admin/customers/${it.customer}`}
                          className="font-medium text-[var(--series-1)] hover:underline"
                        >
                          {it.name}
                        </Link>
                        <span className="text-[var(--text-muted)]"> · {it.detail}</span>
                      </li>
                    ))}
                    {w.count > w.items.length && (
                      <li className="py-1 text-xs text-[var(--text-muted)]">…and {w.count - w.items.length} more</li>
                    )}
                    {w.link && (
                      <li className="pt-1"><Link to={w.link} className={LINK}>Open the list →</Link></li>
                    )}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        </section>

        {visits && (
          <section className={CARD} aria-labelledby="visits-h">
            <div className="mb-2 flex items-baseline justify-between gap-2">
              <h2 id="visits-h" className="text-base font-semibold text-[var(--text-primary)]">Technician visits today</h2>
              <span className="text-xs text-[var(--text-muted)]">from Scheduling</span>
            </div>
            {visits.length === 0 ? (
              <p className="py-6 text-center text-sm text-[var(--text-muted)]">No visits booked today.</p>
            ) : (
              <ul className="divide-y divide-[var(--border-hairline)]">
                {visits.map((v) => (
                  <li key={v.id} className="flex items-center gap-3 py-2.5">
                    <span className="w-12 shrink-0 text-sm font-semibold tabular-nums text-[var(--text-secondary)]">{clock(v.start)}</span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-[var(--text-primary)]">
                        {v.urgent && <span className="mr-1 text-[var(--status-critical)]">URGENT</span>}
                        {v.customer_name || v.title}
                      </p>
                      <p className="truncate text-xs text-[var(--text-muted)]">
                        {v.job_type_label}
                        {v.technicians.length ? ` · ${v.technicians.join(", ")}` : " · nobody assigned"}
                        {v.vehicles.length ? ` · ${v.vehicles.join(", ")}` : ""}
                      </p>
                    </div>
                    <StatusBadge
                      status={v.status === "completed" ? "done" : v.status === "in_progress" ? "in_service" : "new"}
                      label={v.status_label}
                    />
                  </li>
                ))}
              </ul>
            )}
            <Link to="/admin/scheduling" className={`${LINK} mt-2 inline-block`}>Open the schedule →</Link>
          </section>
        )}
      </div>
    </div>
  );
}

function Figure({
  label,
  value,
  sub,
  tone,
  to,
}: {
  label: string;
  value: string;
  sub: string;
  tone?: "good" | "warn" | "bad";
  to?: string;
}) {
  const colour =
    tone === "good" ? "text-[var(--status-good)]"
      : tone === "warn" ? "text-[var(--series-2)]"
      : tone === "bad" ? "text-[var(--status-critical)]"
      : "text-[var(--text-primary)]";
  return (
    <div className="rounded-md bg-[var(--tint-subtle)] p-3">
      <p className="text-sm text-[var(--text-secondary)]">{label}</p>
      <p className={`mt-1 text-2xl font-semibold tabular-nums ${colour}`}>{value}</p>
      {to ? (
        <Link to={to} className="mt-0.5 block truncate text-xs text-[var(--series-1)] hover:underline">{sub}</Link>
      ) : (
        <p className="mt-0.5 truncate text-xs text-[var(--text-muted)]">{sub}</p>
      )}
    </div>
  );
}
