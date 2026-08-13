import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { useApiList } from "../../hooks/useApiList";
import type { Payment } from "../../types";

export function PaymentsPage() {
  const { items, count, loading } = useApiList<Payment>("/payments/?page_size=100&ordering=-date");

  return (
    <div>
      <PageHeader title="Payments" subtitle={`${count} payments recorded`} />
      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Date</TH>
              <TH>Customer</TH>
              <TH>Invoice</TH>
              <TH>Amount</TH>
              <TH>Method</TH>
              <TH>Received by</TH>
            </tr>
          </THead>
          <tbody>
            {items.map((p) => (
              <TR key={p.id}>
                <TD>{new Date(p.date).toLocaleString()}</TD>
                <TD>{p.customer_name}</TD>
                <TD>{p.invoice ?? "—"}</TD>
                <TD className="tabular-nums">R {parseFloat(p.amount).toFixed(2)}</TD>
                <TD className="capitalize">{p.method.replace("_", " ")}</TD>
                <TD>{p.received_by_name ?? "—"}</TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  );
}
