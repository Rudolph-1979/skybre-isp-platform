import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, SortableTH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import type { Invoice, Customer } from "../../types";

interface LineItem {
  description: string;
  quantity: string;
  unit_price: string;
  tax_rate_pct: string;
}

type DateFilterMode = "all" | "30" | "60" | "90" | "custom";

const DATE_FILTER_TABS: { mode: DateFilterMode; label: string }[] = [
  { mode: "all", label: "All" },
  { mode: "30", label: "0–30 days overdue" },
  { mode: "60", label: "0–60 days overdue" },
  { mode: "90", label: "0–90 days overdue" },
  { mode: "custom", label: "Custom date range" },
];

export function InvoicesPage() {
  const [ordering, setOrdering] = useState("-date_created");
  const [dateFilterMode, setDateFilterMode] = useState<DateFilterMode>("all");
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");

  let dateParams = "";
  if (dateFilterMode === "30" || dateFilterMode === "60" || dateFilterMode === "90") {
    dateParams = `&overdue_within_days=${dateFilterMode}`;
  } else if (dateFilterMode === "custom") {
    if (customFrom) dateParams += `&date_created_from=${customFrom}`;
    if (customTo) dateParams += `&date_created_to=${customTo}`;
  }

  const { items, count, loading, refetch } = useApiList<Invoice>(
    `/invoices/?page_size=100&ordering=${ordering}${dateParams}`
  );
  const [customers, setCustomers] = useState<Customer[]>([]);

  function toggleSort(field: string) {
    setOrdering((prev) => (prev === field ? `-${field}` : prev === `-${field}` ? "-date_created" : field));
  }
  const [showModal, setShowModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [customer, setCustomer] = useState("");
  const [dateDue, setDateDue] = useState(new Date().toISOString().slice(0, 10));
  const [lineItems, setLineItems] = useState<LineItem[]>([
    { description: "", quantity: "1", unit_price: "", tax_rate_pct: "15" },
  ]);

  useEffect(() => {
    api.get<{ results: Customer[] }>("/customers/?page_size=200").then((res) => setCustomers(res.data.results));
  }, []);

  function updateItem(idx: number, field: keyof LineItem, value: string) {
    setLineItems((prev) => prev.map((it, i) => (i === idx ? { ...it, [field]: value } : it)));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await api.post("/invoices/", {
        customer,
        date_due: dateDue,
        status: "unpaid",
        items: lineItems.map((it) => ({
          description: it.description,
          quantity: it.quantity,
          unit_price: it.unit_price,
          tax_rate_pct: it.tax_rate_pct,
        })),
      });
      setShowModal(false);
      setLineItems([{ description: "", quantity: "1", unit_price: "", tax_rate_pct: "15" }]);
      refetch();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Invoices"
        subtitle={`${count} invoice${count === 1 ? "" : "s"} matching current filter`}
        actions={
          <button className={btnPrimary} onClick={() => setShowModal(true)}>
            + New invoice
          </button>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {DATE_FILTER_TABS.map((tab) => (
          <button
            key={tab.mode}
            type="button"
            onClick={() => setDateFilterMode(tab.mode)}
            className={
              dateFilterMode === tab.mode
                ? "rounded-md bg-[var(--series-1)] px-3 py-1.5 text-sm font-medium text-white"
                : "rounded-md border border-[var(--baseline)] px-3 py-1.5 text-sm font-medium text-[var(--text-primary)] hover:bg-black/5"
            }
          >
            {tab.label}
          </button>
        ))}
      </div>

      {dateFilterMode === "custom" && (
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <FormField label="Invoice date from">
            <input
              type="date"
              className={inputClass}
              value={customFrom}
              onChange={(e) => setCustomFrom(e.target.value)}
            />
          </FormField>
          <FormField label="Invoice date to">
            <input
              type="date"
              className={inputClass}
              value={customTo}
              onChange={(e) => setCustomTo(e.target.value)}
            />
          </FormField>
          {(customFrom || customTo) && (
            <button
              type="button"
              className={`${btnSecondary} mb-3`}
              onClick={() => {
                setCustomFrom("");
                setCustomTo("");
              }}
            >
              Clear
            </button>
          )}
        </div>
      )}

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <SortableTH field="number" ordering={ordering} onSort={toggleSort}>Number</SortableTH>
              <SortableTH field="customer__full_name" ordering={ordering} onSort={toggleSort}>Customer</SortableTH>
              <SortableTH field="date_due" ordering={ordering} onSort={toggleSort}>Due date</SortableTH>
              <SortableTH field="total" ordering={ordering} onSort={toggleSort}>Total</SortableTH>
              <SortableTH field="paid_amount" ordering={ordering} onSort={toggleSort}>Paid</SortableTH>
              <SortableTH field="status" ordering={ordering} onSort={toggleSort}>Status</SortableTH>
            </tr>
          </THead>
          <tbody>
            {items.map((inv) => (
              <TR key={inv.id}>
                <TD>
                  <Link to={`/admin/invoices/${inv.id}`} className="font-medium text-[var(--series-1)] hover:underline">
                    {inv.number}
                  </Link>
                </TD>
                <TD>{inv.customer_name}</TD>
                <TD>{inv.date_due}</TD>
                <TD className="tabular-nums">R {parseFloat(inv.total).toFixed(2)}</TD>
                <TD className="tabular-nums">R {parseFloat(inv.paid_amount).toFixed(2)}</TD>
                <TD><StatusBadge status={inv.status} /></TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title="New invoice" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Customer">
              <select className={inputClass} required value={customer} onChange={(e) => setCustomer(e.target.value)}>
                <option value="">Select customer…</option>
                {customers.map((c) => (
                  <option key={c.id} value={c.id}>{c.full_name} ({c.customer_id})</option>
                ))}
              </select>
            </FormField>
            <FormField label="Due date">
              <input type="date" className={inputClass} required value={dateDue} onChange={(e) => setDateDue(e.target.value)} />
            </FormField>

            <p className="mb-2 text-sm font-medium text-[var(--text-secondary)]">Line items</p>
            {lineItems.map((item, idx) => (
              <div key={idx} className="mb-2 grid grid-cols-[2fr_1fr_1fr] gap-2">
                <input
                  className={inputClass}
                  placeholder="Description"
                  required
                  value={item.description}
                  onChange={(e) => updateItem(idx, "description", e.target.value)}
                />
                <input
                  className={inputClass}
                  placeholder="Qty"
                  type="number"
                  value={item.quantity}
                  onChange={(e) => updateItem(idx, "quantity", e.target.value)}
                />
                <input
                  className={inputClass}
                  placeholder="Price"
                  type="number"
                  step="0.01"
                  required
                  value={item.unit_price}
                  onChange={(e) => updateItem(idx, "unit_price", e.target.value)}
                />
              </div>
            ))}
            <button
              type="button"
              className="mb-4 text-xs font-medium text-[var(--series-1)] hover:underline"
              onClick={() => setLineItems([...lineItems, { description: "", quantity: "1", unit_price: "", tax_rate_pct: "15" }])}
            >
              + Add line item
            </button>

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>Cancel</button>
              <button type="submit" disabled={saving} className={btnPrimary}>{saving ? "Saving…" : "Create invoice"}</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
