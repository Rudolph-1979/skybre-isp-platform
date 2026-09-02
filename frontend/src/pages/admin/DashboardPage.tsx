import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar } from "recharts";
import { api } from "../../api/client";
import { StatCard } from "../../components/StatCard";
import { PageHeader } from "../../components/PageHeader";
import { palette } from "../../palette";
import { useAuth } from "../../context/AuthContext";
import { canAccessSection } from "../../utils/permissions";
import { Modal } from "../../components/Modal";
import type { CustomerGrowth, DashboardSummary, HighAlertCustomers, OfflineCustomers, Section, UpcomingBlocks } from "../../types";

function formatCurrency(v: number) {
  return new Intl.NumberFormat("en-ZA", { style: "currency", currency: "ZAR", maximumFractionDigits: 0 }).format(v);
}

// How far ahead the blocking panel looks. The tile itself always shows
// TOMORROW; the horizon only widens the drill-down list, so there's a chase
// window instead of a same-day surprise.
const BLOCK_HORIZON_DAYS = 7;

function whenLabel(daysUntil: number) {
  if (daysUntil === 0) return "Tomorrow";
  if (daysUntil === 1) return "In 2 days";
  return `In ${daysUntil + 1} days`;
}

export function DashboardPage() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [growth, setGrowth] = useState<CustomerGrowth | null>(null);
  const [growthError, setGrowthError] = useState("");
  const [alerts, setAlerts] = useState<HighAlertCustomers | null>(null);
  const [alertsError, setAlertsError] = useState("");
  const [offline, setOffline] = useState<OfflineCustomers | null>(null);
  const [blocks, setBlocks] = useState<UpcomingBlocks | null>(null);
  const [blocksError, setBlocksError] = useState("");
  const [showBlocks, setShowBlocks] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const { user } = useAuth();

  /**
   * A tile's click handler, or undefined when it shouldn't be clickable.
   *
   * Gated on the section the destination lives in, mirroring the backend's
   * own check: offering a click that lands on "you don't have access to this
   * section" is worse than not offering it. Restricted staff just get a plain
   * tile, which still tells them the number.
   */
  function goTo(section: Section, to: string) {
    return canAccessSection(user, section) ? () => navigate(to) : undefined;
  }

  function actionFor(section: Section, label: string) {
    return canAccessSection(user, section) ? label : undefined;
  }

  useEffect(() => {
    api.get<DashboardSummary>("/dashboard-summary/")
      .then((res) => setSummary(res.data))
      .catch(() => setSummary(null));

    // Aggregated server-side (see the backend's CustomerGrowthView) rather
    // than counted here: the customer list endpoint caps page_size at 500,
    // so counting in the browser silently under-reports once there are
    // more customers than one page -- which is what went wrong with the
    // charts this replaced.
    api.get<CustomerGrowth>("/customer-growth/?months=12")
      .then((res) => setGrowth(res.data))
      .catch(() => setGrowthError("Could not load customer growth figures."));

    api.get<OfflineCustomers>("/offline-customers/?hours=24")
      .then((res) => setOffline(res.data))
      .catch(() => setOffline(null));

    api.get<HighAlertCustomers>("/high-alert-customers/?months=6&min_tickets=3")
      .then((res) => setAlerts(res.data))
      .catch(() => setAlertsError("Could not load high alert customers."));

    // Finance-gated on the backend, so staff without that section get a 403.
    // Treated as "nothing to show" rather than an error banner — it isn't a
    // fault, they just can't see it.
    api.get<UpcomingBlocks>(`/upcoming-blocks/?days=${BLOCK_HORIZON_DAYS}`)
      .then((res) => setBlocks(res.data))
      .catch((err) => {
        if (err?.response?.status !== 403) {
          setBlocksError("Could not load upcoming blocks.");
        }
      });
  }, []);

  // Deep link: Conecto (and anything else) can send you straight to the open
  // list with /admin?panel=blocks rather than dropping you on the dashboard
  // and making you find the tile. The param is cleared once consumed so a
  // refresh doesn't keep reopening it.
  useEffect(() => {
    if (searchParams.get("panel") !== "blocks") return;
    if (!blocks) return;                    // wait for the data to arrive
    setShowBlocks(true);
    const next = new URLSearchParams(searchParams);
    next.delete("panel");
    setSearchParams(next, { replace: true });
  }, [searchParams, blocks, setSearchParams]);

  if (!summary) return <p className="text-[var(--text-muted)]">Loading dashboard…</p>;

  return (
    <div>
      <PageHeader title="Dashboard" subtitle="Overview of customers, billing, network health, and support." />

      <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
        {/* Each tile goes to the list its number came from, with the filter
            already applied -- so "1 unpaid" lands on that one unpaid invoice,
            not on every document you have. */}
        <StatCard
          label="Total customers"
          value={summary.customers_total}
          sublabel={`${summary.customers_active} active`}
          onClick={goTo("customers", "/admin/customers")}
          actionLabel={actionFor("customers", "View customers")}
        />
        <StatCard
          label="Active services"
          value={summary.services_active}
          accent="series-3"
          onClick={goTo("services", "/admin/services?status=active")}
          actionLabel={actionFor("services", "View active services")}
        />
        <StatCard
          label="Revenue this month"
          value={formatCurrency(summary.revenue_this_month)}
          accent="status-good"
          // Revenue is money received, so the payments list is where it comes
          // from -- not the invoice list, which is money billed.
          onClick={goTo("finance", "/admin/finance?tab=payments")}
          actionLabel={actionFor("finance", "View payments")}
        />
        <StatCard
          label="Outstanding balance"
          value={formatCurrency(summary.outstanding_balance)}
          sublabel={`${summary.invoices_unpaid} unpaid · ${summary.invoices_overdue} overdue`}
          accent="status-warning"
          onClick={goTo("finance", "/admin/finance?doc=invoice&status=unpaid")}
          actionLabel={actionFor("finance", "View unpaid invoices")}
        />
        <StatCard
          label="Devices online"
          value={`${summary.devices_online}/${summary.devices_total}`}
          accent={summary.devices_offline > 0 ? "status-critical" : "status-good"}
          onClick={goTo("networking", "/admin/networking?tab=devices")}
          actionLabel={actionFor("networking", "View devices")}
        />
        <StatCard
          label="Open tickets"
          value={summary.tickets_open}
          sublabel={`${summary.tickets_urgent} urgent`}
          accent="status-warning"
          onClick={goTo("tickets", "/admin/tickets?status=open")}
          actionLabel={actionFor("tickets", "View open tickets")}
        />
        <StatCard
          label="Follow-ups due"
          value={summary.leads_follow_up_due}
          // A follow-up nobody is reminded of is the same as no follow-up:
          // the date gets set, the day passes, and the lead goes cold
          // without anything ever saying so. This tile is the reminder.
          sublabel={
            summary.leads_follow_up_due > 0
              ? `of ${summary.leads_open} open leads`
              : `${summary.leads_open} open leads, nothing due`
          }
          accent={summary.leads_follow_up_due > 0 ? "status-warning" : "series-1"}
          onClick={goTo("sales", "/admin/leads")}
          actionLabel={actionFor("sales", "Work the list")}
        />
        <StatCard
          label="Bad debt"
          value={summary.customers_bad_debt}
          // The money, not the headcount, is what anyone reacts to -- "3
          // customers" says far less than "3 customers, R14,200 written off".
          sublabel={
            summary.customers_bad_debt > 0
              ? `${formatCurrency(summary.customers_bad_debt_value)} written off`
              : "Nothing written off"
          }
          accent={summary.customers_bad_debt > 0 ? "status-critical" : "series-1"}
          onClick={goTo("customers", "/admin/customers?status=bad_debt")}
          actionLabel={actionFor("customers", "View bad debt")}
        />
        <StatCard
          label="Cancelled customers"
          value={summary.customers_cancelled}
          sublabel={
            summary.customers_cancelled_recently > 0
              ? `${summary.customers_cancelled_recently} in the last 30 days`
              : "None in the last 30 days"
          }
          // The total alone only ever goes up and says nothing; the recent
          // figure beside it is the one worth reacting to.
          accent={summary.customers_cancelled_recently > 0 ? "status-warning" : "series-1"}
          onClick={goTo("customers", "/admin/customers?status=inactive")}
          actionLabel={actionFor("customers", "View cancelled")}
        />
        <StatCard
          label="Offline customers"
          value={offline ? offline.count : "—"}
          sublabel={
            offline
              ? offline.count === 0
                ? "Everyone is online"
                : "Dropped in the last 24h and not back"
              : "Loading…"
          }
          accent={offline && offline.count > 0 ? "status-critical" : "status-good"}
          onClick={goTo("customers", "/admin/offline-customers")}
          actionLabel={actionFor("customers", "Call them")}
        />
        <StatCard
          label="High alert customers"
          value={alerts ? alerts.count : "—"}
          sublabel={
            alertsError
              ? "Couldn't load"
              : alerts
                ? `${alerts.min_tickets}+ tickets in a month, last ${alerts.months} months`
                : "Loading…"
          }
          accent="status-critical"
          // Lands on the real customer list filtered to exactly these people,
          // where every other tool on that page -- search, columns, partner
          // filter, bulk select -- applies to them. The ranked panel that
          // used to sit on this page could only be read.
          // Also clickable at zero, for the same reason as the blocking tile
          // below: an empty filtered list says "nobody qualifies", which a
          // dead tile does not.
          onClick={alerts ? goTo("customers", "/admin/customers?high_alert=1") : undefined}
          actionLabel={alerts ? actionFor("customers", "See who") : undefined}
        />
        {/* Only rendered once the request has come back. Staff without
            Finance access get a 403 and no tile at all, rather than a tile
            reading "—" forever. */}
        {blocks && (
          <StatCard
            label="Blocking tomorrow"
            value={blocks.count_tomorrow}
            sublabel={
              blocks.auto_suspend_enabled
                ? `${blocks.count_horizon} within ${blocks.horizon_days} days`
                : `${blocks.count_horizon} within ${blocks.horizon_days} days · auto-suspension OFF`
            }
            accent={blocks.count_tomorrow > 0 ? "status-critical" : "status-good"}
            // Clickable even at zero. A tile that quietly does nothing when
            // the number happens to be 0 is indistinguishable from a broken
            // one -- and "nobody currently meets the blocking criteria" is
            // real information, especially when auto-suspension is off.
            onClick={canAccessSection(user, "finance") ? () => setShowBlocks(true) : undefined}
            actionLabel={actionFor("finance", "Show the list")}
          />
        )}
      </div>

      {blocksError && (
        <p className="mb-4 text-sm text-[var(--status-critical)]">{blocksError}</p>
      )}

      {/* Full width since the high-alert panel that used to sit beside it
          was removed -- a two-thirds card with empty space next to it reads
          as something failing to load. */}
      <div className="grid grid-cols-1 gap-4">
        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">Customer growth — new customers per month</h2>
          <p className="mb-3 mt-0.5 text-xs text-[var(--text-muted)]">
            {growth ? (
              <>
                {growth.total_new} new in the last 12 months
                {growth.estimated_from_created_at > 0 && (
                  <>
                    {" · "}
                    {/* Deliberately normal secondary ink, not the warning
                        token: #fab219 on this surface is ~1.9:1 contrast,
                        unreadable at 12px. The wording carries the caveat. */}
                    <span className="font-medium text-[var(--text-secondary)]">
                      {growth.estimated_from_created_at} counted on their import date, not a real signup date
                    </span>
                  </>
                )}
              </>
            ) : (
              "Last 12 months"
            )}
          </p>

          {growthError ? (
            <p className="text-sm text-[var(--status-critical)]">{growthError}</p>
          ) : !growth ? (
            <p className="text-sm text-[var(--text-muted)]">Loading…</p>
          ) : (
            /* Single series, so no legend -- the heading names it. Height
               includes the x-axis band so the card never grows an inner
               scrollbar. */
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={growth.buckets} margin={{ top: 4, right: 8, left: 0, bottom: 0 }} barCategoryGap="34%">
                <CartesianGrid stroke={palette.gridline} vertical={false} />
                <XAxis
                  dataKey="label"
                  tick={{ fontSize: 11, fill: palette.textMuted }}
                  axisLine={{ stroke: palette.gridline }}
                  tickLine={false}
                />
                {/* allowDecimals={false}: these are whole customers, so
                    ticks like "1.5" would be nonsense. */}
                <YAxis
                  tick={{ fontSize: 11, fill: palette.textMuted }}
                  axisLine={false}
                  tickLine={false}
                  width={32}
                  allowDecimals={false}
                />
                <Tooltip
                  contentStyle={{ fontSize: 12, borderRadius: 8 }}
                  formatter={(v) => [`${v} new customer${Number(v) === 1 ? "" : "s"}`, ""]}
                  labelFormatter={(l) => String(l)}
                  cursor={{ fill: palette.gridline, fillOpacity: 0.35 }}
                />
                {/* Slim bars: a saturated fill this size reads as an
                    accent rather than a heavy block (see the dataviz
                    "thin marks" rule). */}
                <Bar dataKey="new_customers" fill={palette.series1} radius={[4, 4, 0, 0]} maxBarSize={26} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>

      </div>

      {showBlocks && blocks && (
        <Modal
          title={`Blocking within ${blocks.horizon_days} days`}
          onClose={() => setShowBlocks(false)}
        >
          {!blocks.auto_suspend_enabled && (
            <p className="mb-3 rounded-md border border-[#b3852e] bg-[var(--surface-2)] px-3 py-2 text-xs text-[var(--text-secondary)]">
              <span className="font-semibold text-[#b3852e]">Auto-suspension is off.</span> These
              customers meet the blocking criteria but will <strong>not</strong> actually be cut off
              while it stays off. Turn it on under Configs → Billing → Auto-suspension.
            </p>
          )}

          <p className="mb-3 text-xs text-[var(--text-muted)]">
            A customer is blocked once an invoice has been overdue longer than their blocking period
            <em> and</em> their balance is worse than their minimum balance. Dates assume a billing run
            happens each day.
          </p>

          {blocks.results.length === 0 && (
            <p className="rounded-md border border-[var(--border-hairline)] bg-[var(--surface-2)] px-3 py-4 text-sm text-[var(--text-secondary)]">
              <strong className="text-[var(--text-primary)]">Nobody is due to be blocked.</strong>{" "}
              No customer currently has an invoice overdue for longer than their blocking period
              <em> and</em> a balance worse than their minimum, so there is nothing scheduled in the
              next {blocks.horizon_days} days.
              {!blocks.auto_suspend_enabled && (
                <>
                  {" "}
                  Note that auto-suspension is off, so even a customer who did meet the criteria
                  would not actually be cut off.
                </>
              )}
            </p>
          )}

          <ul className="divide-y divide-[var(--border-hairline)]">
            {blocks.results.map((b) => (
              <li key={b.customer} className="flex items-start justify-between gap-3 py-2.5">
                <div className="min-w-0">
                  <Link
                    to={`/admin/customers/${b.customer}`}
                    className="text-sm font-medium text-[var(--text-primary)] hover:underline"
                    onClick={() => setShowBlocks(false)}
                  >
                    {b.name}
                  </Link>
                  <p className="text-xs text-[var(--text-muted)]">
                    {b.reference} · {b.invoices_owing} invoice{b.invoices_owing === 1 ? "" : "s"} owing
                    {" · oldest due "}
                    {b.oldest_invoice_due}
                  </p>
                  <p className="text-xs text-[var(--text-muted)]">
                    {b.active_services} active service{b.active_services === 1 ? "" : "s"} would be
                    suspended · {b.blocking_period_days}-day blocking period
                  </p>
                </div>
                <div className="shrink-0 text-right">
                  <p
                    className={`text-sm font-semibold ${
                      b.days_until === 0 ? "text-[var(--status-critical)]" : "text-[var(--text-primary)]"
                    }`}
                  >
                    {whenLabel(b.days_until)}
                  </p>
                  <p className="text-xs text-[var(--text-muted)]">{b.block_date}</p>
                  <p className="tabular-nums text-xs font-medium text-[var(--text-secondary)]">
                    {formatCurrency(parseFloat(b.balance))} owing
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </Modal>
      )}
    </div>
  );
}
