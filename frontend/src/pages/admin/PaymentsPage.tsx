import { useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, SortableTH, TR, TD } from "../../components/Table";
import { useApiList } from "../../hooks/useApiList";
import { filterSelectClass, btnSecondary } from "../../components/Modal";
import { ColumnToggle, type ColumnDef } from "../../components/ColumnToggle";
import { useColumnVisibility } from "../../hooks/useColumnVisibility";
import type { Payment } from "../../types";

const COLUMNS: ColumnDef[] = [
  { key: "date", label: "Date" },
  { key: "customer", label: "Customer" },
  { key: "invoice", label: "Invoice" },
  { key: "amount", label: "Amount" },
  { key: "method", label: "Method" },
  { key: "received_by", label: "Received by" },
];

export function PaymentsPage() {
  const [ordering, setOrdering] = useState("-date");
  const [methodFilter, setMethodFilter] = useState("");
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("payments", ["date"]);
  const { items, count, loading } = useApiList<Payment>(
    `/payments/?page_size=100&ordering=${ordering}${methodFilter ? `&method=${methodFilter}` : ""}`
  );

  function toggleSort(field: string) {
    setOrdering((prev) => (prev === field ? `-${field}` : prev === `-${field}` ? "-date" : field));
  }

  return (
    <div>
      <PageHeader title="Payments" subtitle={`${count} payments recorded`} />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select className={filterSelectClass} value={methodFilter} onChange={(e) => setMethodFilter(e.target.value)}>
          <option value="">All methods</option>
          <option value="cash">Cash</option>
          <option value="card">Card</option>
          <option value="bank_transfer">Bank transfer</option>
          <option value="mobile_money">Mobile money</option>
          <option value="manual">Manual</option>
        </select>
        {methodFilter && (
          <button type="button" className={btnSecondary} onClick={() => setMethodFilter("")}>
            Clear filters
          </button>
        )}
        <div className="ml-auto">
          <ColumnToggle columns={COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["date"]} />
        </div>
      </div>
      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <SortableTH field="date" ordering={ordering} onSort={toggleSort}>Date</SortableTH>
              {isVisible("customer") && <SortableTH field="customer__full_name" ordering={ordering} onSort={toggleSort}>Customer</SortableTH>}
              {isVisible("invoice") && <TH>Invoice</TH>}
              {isVisible("amount") && <SortableTH field="amount" ordering={ordering} onSort={toggleSort}>Amount</SortableTH>}
              {isVisible("method") && <SortableTH field="method" ordering={ordering} onSort={toggleSort}>Method</SortableTH>}
              {isVisible("received_by") && <SortableTH field="received_by__username" ordering={ordering} onSort={toggleSort}>Received by</SortableTH>}
            </tr>
          </THead>
          <tbody>
            {items.map((p) => (
              <TR key={p.id}>
                <TD>{new Date(p.date).toLocaleString()}</TD>
                {isVisible("customer") && <TD>{p.customer_name}</TD>}
                {isVisible("invoice") && <TD>{p.invoice ?? "—"}</TD>}
                {isVisible("amount") && <TD className="tabular-nums">R {parseFloat(p.amount).toFixed(2)}</TD>}
                {isVisible("method") && <TD className="capitalize">{p.method.replace("_", " ")}</TD>}
                {isVisible("received_by") && <TD>{p.received_by_name ?? "—"}</TD>}
              </TR>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  );
}
