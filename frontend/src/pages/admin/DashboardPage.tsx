import { useEffect, useState } from "react";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar, Cell } from "recharts";
import { api } from "../../api/client";
import { StatCard } from "../../components/StatCard";
import { PageHeader } from "../../components/PageHeader";
import { palette } from "../../palette";
import type { DashboardSummary, Payment, Ticket } from "../../types";

function formatCurrency(v: number) {
  return new Intl.NumberFormat("en-ZA", { style: "currency", currency: "ZAR", maximumFractionDigits: 0 }).format(v);
}

export function DashboardPage() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [revenueTrend, setRevenueTrend] = useState<{ day: string; revenue: number }[]>([]);
  const [ticketsByStatus, setTicketsByStatus] = useState<{ status: string; count: number; fill: string }[]>([]);

  useEffect(() => {
    api.get<DashboardSummary>("/dashboard-summary/").then((res) => setSummary(res.data));

    api.get<{ results: Payment[] }>("/payments/?page_size=200&ordering=-date").then((res) => {
      const buckets = new Map<string, number>();
      const today = new Date();
      for (let i = 13; i >= 0; i--) {
        const d = new Date(today);
        d.setDate(d.getDate() - i);
        buckets.set(d.toISOString().slice(0, 10), 0);
      }
      res.data.results.forEach((p) => {
        const day = p.date.slice(0, 10);
        if (buckets.has(day)) buckets.set(day, (buckets.get(day) ?? 0) + parseFloat(p.amount));
      });
      setRevenueTrend(
        Array.from(buckets.entries()).map(([day, revenue]) => ({
          day: day.slice(5),
          revenue: Math.round(revenue),
        }))
      );
    });

    api.get<{ results: Ticket[] }>("/tickets/?page_size=500").then((res) => {
      const statusColor: Record<string, string> = {
        open: palette.statusWarning,
        pending: palette.statusWarning,
        resolved: palette.statusGood,
        closed: palette.textMuted,
      };
      const counts = new Map<string, number>([
        ["open", 0],
        ["pending", 0],
        ["resolved", 0],
        ["closed", 0],
      ]);
      res.data.results.forEach((t) => counts.set(t.status, (counts.get(t.status) ?? 0) + 1));
      setTicketsByStatus(
        Array.from(counts.entries()).map(([status, count]) => ({ status, count, fill: statusColor[status] }))
      );
    });
  }, []);

  if (!summary) return <p className="text-[var(--text-muted)]">Loading dashboard…</p>;

  return (
    <div>
      <PageHeader title="Dashboard" subtitle="Overview of customers, billing, network health, and support." />

      <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="Total customers" value={summary.customers_total} sublabel={`${summary.customers_active} active`} />
        <StatCard label="Active services" value={summary.services_active} accent="series-3" />
        <StatCard label="Revenue this month" value={formatCurrency(summary.revenue_this_month)} accent="status-good" />
        <StatCard
          label="Outstanding balance"
          value={formatCurrency(summary.outstanding_balance)}
          sublabel={`${summary.invoices_unpaid} unpaid · ${summary.invoices_overdue} overdue`}
          accent="status-warning"
        />
        <StatCard
          label="Devices online"
          value={`${summary.devices_online}/${summary.devices_total}`}
          accent={summary.devices_offline > 0 ? "status-critical" : "status-good"}
        />
        <StatCard label="Open tickets" value={summary.tickets_open} sublabel={`${summary.tickets_urgent} urgent`} accent="status-warning" />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm lg:col-span-2">
          <h2 className="mb-3 text-sm font-semibold text-[var(--text-primary)]">Revenue collected — last 14 days</h2>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={revenueTrend} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={palette.gridline} vertical={false} />
              <XAxis dataKey="day" tick={{ fontSize: 11, fill: palette.textMuted }} axisLine={{ stroke: palette.gridline }} tickLine={false} />
              <YAxis tick={{ fontSize: 11, fill: palette.textMuted }} axisLine={false} tickLine={false} width={40} />
              <Tooltip formatter={(v) => formatCurrency(Number(v))} contentStyle={{ fontSize: 12, borderRadius: 8 }} />
              <Line type="monotone" dataKey="revenue" stroke={palette.series1} strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>

        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold text-[var(--text-primary)]">Tickets by status</h2>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={ticketsByStatus} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={palette.gridline} vertical={false} />
              <XAxis dataKey="status" tick={{ fontSize: 11, fill: palette.textMuted }} axisLine={{ stroke: palette.gridline }} tickLine={false} />
              <YAxis tick={{ fontSize: 11, fill: palette.textMuted }} axisLine={false} tickLine={false} width={30} />
              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
              <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                {ticketsByStatus.map((entry) => (
                  <Cell key={entry.status} fill={entry.fill} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
