import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../../api/client";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import type { Customer, Service, Invoice, Payment, Ticket } from "../../types";

export function CustomerDetailPage() {
  const { id } = useParams();
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [services, setServices] = useState<Service[]>([]);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [tickets, setTickets] = useState<Ticket[]>([]);

  useEffect(() => {
    if (!id) return;
    api.get<Customer>(`/customers/${id}/`).then((res) => setCustomer(res.data));
    api.get<{ results: Service[] }>(`/services/?customer=${id}`).then((res) => setServices(res.data.results));
    api.get<{ results: Invoice[] }>(`/invoices/?customer=${id}&ordering=-date_created`).then((res) => setInvoices(res.data.results));
    api.get<{ results: Payment[] }>(`/payments/?customer=${id}&ordering=-date`).then((res) => setPayments(res.data.results));
    api.get<{ results: Ticket[] }>(`/tickets/?customer=${id}`).then((res) => setTickets(res.data.results));
  }, [id]);

  if (!customer) return <p className="text-[var(--text-muted)]">Loading…</p>;

  return (
    <div>
      <Link to="/admin/customers" className="mb-4 inline-block text-sm text-[var(--series-1)] hover:underline">
        ← Back to customers
      </Link>
      <PageHeader
        title={customer.full_name}
        subtitle={`${customer.customer_id} · ${customer.email} · ${customer.phone}`}
        actions={<StatusBadge status={customer.status} />}
      />

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
    </div>
  );
}
