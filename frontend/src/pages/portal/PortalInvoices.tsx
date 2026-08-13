import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import type { Invoice, Payment } from "../../types";

export function PortalInvoices() {
  const { user } = useAuth();
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [payments, setPayments] = useState<Payment[]>([]);

  useEffect(() => {
    if (!user?.customer_id) return;
    api.get<{ results: Invoice[] }>(`/invoices/?customer=${user.customer_id}&ordering=-date_created`).then((res) => setInvoices(res.data.results));
    api.get<{ results: Payment[] }>(`/payments/?customer=${user.customer_id}&ordering=-date`).then((res) => setPayments(res.data.results));
  }, [user]);

  return (
    <div>
      <PageHeader title="Invoices & Payments" subtitle="Your billing history." />

      <h2 className="mb-2 text-sm font-semibold">Invoices</h2>
      <Table>
        <THead>
          <tr>
            <TH>Number</TH>
            <TH>Due date</TH>
            <TH>Total</TH>
            <TH>Balance due</TH>
            <TH>Status</TH>
          </tr>
        </THead>
        <tbody>
          {invoices.map((inv) => (
            <TR key={inv.id}>
              <TD className="font-medium">{inv.number}</TD>
              <TD>{inv.date_due}</TD>
              <TD className="tabular-nums">R {parseFloat(inv.total).toFixed(2)}</TD>
              <TD className="tabular-nums">R {parseFloat(inv.balance_due).toFixed(2)}</TD>
              <TD><StatusBadge status={inv.status} /></TD>
            </TR>
          ))}
          {invoices.length === 0 && <TR><TD className="text-[var(--text-muted)]">No invoices yet.</TD></TR>}
        </tbody>
      </Table>

      <h2 className="mb-2 mt-6 text-sm font-semibold">Payment history</h2>
      <Table>
        <THead>
          <tr>
            <TH>Date</TH>
            <TH>Amount</TH>
            <TH>Method</TH>
          </tr>
        </THead>
        <tbody>
          {payments.map((p) => (
            <TR key={p.id}>
              <TD>{new Date(p.date).toLocaleDateString()}</TD>
              <TD className="tabular-nums">R {parseFloat(p.amount).toFixed(2)}</TD>
              <TD className="capitalize">{p.method.replace("_", " ")}</TD>
            </TR>
          ))}
          {payments.length === 0 && <TR><TD className="text-[var(--text-muted)]">No payments yet.</TD></TR>}
        </tbody>
      </Table>
    </div>
  );
}
