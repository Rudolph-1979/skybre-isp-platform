import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { StatCard } from "../../components/StatCard";
import { StatusBadge } from "../../components/StatusBadge";
import { PageHeader } from "../../components/PageHeader";
import type { Customer, Service, Invoice } from "../../types";

export function PortalDashboard() {
  const { user } = useAuth();
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [services, setServices] = useState<Service[]>([]);
  const [latestInvoice, setLatestInvoice] = useState<Invoice | null>(null);

  useEffect(() => {
    if (!user?.customer_id) return;
    api.get<Customer>(`/customers/${user.customer_id}/`).then((res) => setCustomer(res.data));
    api.get<{ results: Service[] }>(`/services/?customer=${user.customer_id}`).then((res) => setServices(res.data.results));
    api
      .get<{ results: Invoice[] }>(`/invoices/?customer=${user.customer_id}&ordering=-date_created&page_size=1`)
      .then((res) => setLatestInvoice(res.data.results[0] ?? null));
  }, [user]);

  if (!customer) return <p className="text-[var(--text-muted)]">Loading…</p>;

  return (
    <div>
      <PageHeader title={`Welcome back, ${customer.full_name.split(" ")[0]}`} subtitle={`Account ${customer.customer_id}`} />

      <div className="mb-6 grid grid-cols-3 gap-4">
        <StatCard label="Account status" value={customer.status} accent={customer.status === "active" ? "status-good" : "status-warning"} />
        <StatCard label="Balance owed" value={`R ${parseFloat(customer.balance).toFixed(2)}`} accent={parseFloat(customer.balance) > 0 ? "status-warning" : "status-good"} />
        <StatCard label="Active services" value={services.filter((s) => s.status === "active").length} />
      </div>

      <h2 className="mb-2 text-sm font-semibold">Your services</h2>
      <div className="mb-6 space-y-2">
        {services.map((s) => (
          <div key={s.id} className="flex items-center justify-between rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4">
            <div>
              <p className="font-medium">{s.tariff_name}</p>
              <p className="text-sm text-[var(--text-muted)]">R {parseFloat(s.price).toFixed(2)} / month</p>
            </div>
            <StatusBadge status={s.status} />
          </div>
        ))}
        {services.length === 0 && <p className="text-sm text-[var(--text-muted)]">No services on your account yet.</p>}
      </div>

      {latestInvoice && (
        <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-[var(--text-muted)]">Latest invoice</p>
              <p className="font-medium">{latestInvoice.number} — R {parseFloat(latestInvoice.total).toFixed(2)}</p>
            </div>
            <div className="flex items-center gap-3">
              <StatusBadge status={latestInvoice.status} />
              <Link to="/portal/invoices" className="text-sm text-[var(--series-1)] hover:underline">View all →</Link>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
