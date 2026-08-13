import { useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, SortableTH, TR, TD } from "../../components/Table";
import { useApiList } from "../../hooks/useApiList";
import type { Payment } from "../../types";

export function PaymentsPage() {
  const [ordering, setOrdering] = useState("-date");
  const { items, count, loading } = useApiList<Payment>(`/payments/?page_size=100&ordering=${ordering}`);

  function toggleSort(field: string) {
    setOrdering((prev) => (prev === field ? `-${field}` : prev === `-${field}` ? "-date" : field));
  }

  return (
    <div>
      <PageHeader title="Payments" subtitle={`${count} payments recorded`} />
      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <SortableTH field="date" ordering={ordering} onSort={toggleSort}>Date</SortableTH>
              <SortableTH field="customer__full_name" ordering={ordering} onSort={toggleSort}>Customer</SortableTH>
              <TH>Invoice</TH>
              <SortableTH field="amount" ordering={ordering} onSort={toggleSort}>Amount</SortableTH>
              <SortableTH field="method" ordering={ordering} onSort={toggleSort}>Method</SortableTH>
              <SortableTH field="received_by__username" ordering={ordering} onSort={toggleSort}>Received by</SortableTH>
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
