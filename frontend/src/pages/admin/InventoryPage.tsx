import { useEffect, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, SortableTH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, filterSelectClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { ColumnToggle, type ColumnDef } from "../../components/ColumnToggle";
import { useColumnVisibility } from "../../hooks/useColumnVisibility";
import type { Supplier, Product, SerializedUnit, StockReceipt, StockIssue, Job, User } from "../../types";

type Tab = "products" | "units" | "suppliers" | "receipts" | "issues";

type NewAction = { label: string; onClick: () => void } | null;

const CATEGORY_LABEL: Record<Product["category"], string> = {
  router: "Router / CPE",
  ont: "ONT",
  cable: "Cable",
  connector: "Connector",
  tool: "Tool / Equipment",
  other: "Other",
};

export function InventoryPage() {
  const [tab, setTab] = useState<Tab>("products");
  const [newAction, setNewAction] = useState<NewAction>(null);
  const TABS: { key: Tab; label: string }[] = [
    { key: "products", label: "Products" },
    { key: "units", label: "Units (serial / MAC)" },
    { key: "suppliers", label: "Suppliers" },
    { key: "receipts", label: "Stock Receipts" },
    { key: "issues", label: "Stock Issues" },
  ];

  return (
    <div>
      <PageHeader
        title="Stock / Inventory"
        subtitle="Equipment and consumables — checked in against supplier invoices, issued out to jobs and technicians."
        actions={
          newAction && (
            <button className={btnPrimary} onClick={newAction.onClick}>
              {newAction.label}
            </button>
          )
        }
      />
      <div className="mb-4 flex gap-1 border-b border-[var(--border-hairline)]">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium ${
              tab === t.key
                ? "border-b-2 border-[var(--series-1)] text-[var(--text-primary)]"
                : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === "products" && <ProductsTab onRegisterNewAction={setNewAction} />}
      {tab === "units" && <UnitsTab onRegisterNewAction={setNewAction} />}
      {tab === "suppliers" && <SuppliersTab onRegisterNewAction={setNewAction} />}
      {tab === "receipts" && <ReceiptsTab onRegisterNewAction={setNewAction} />}
      {tab === "issues" && <IssuesTab onRegisterNewAction={setNewAction} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Products
// ---------------------------------------------------------------------------

// One physical unit being checked in. Held as a pair of fields rather than
// parsed out of a "SERIAL,MAC" text blob, which is what this form used to
// ask for -- that syntax was in a label nobody reads, so MACs mostly never
// got captured.
type SerialUnitForm = { serial: string; mac: string };
const EMPTY_SERIAL_UNIT: SerialUnitForm = { serial: "", mac: "" };

// Canonical form is upper-case colon-separated, matching the backend's
// inventory/identifiers.py. Done on blur rather than on every keystroke so
// the caret doesn't jump around while someone is typing or a scanner is
// filling the field.
function tidyMac(value: string) {
  const hex = value.replace(/[\s:.\-_]/g, "").toUpperCase();
  if (!/^[0-9A-F]{12}$/.test(hex)) return value.trim();
  return (hex.match(/.{2}/g) as string[]).join(":");
}

function macLooksWrong(value: string) {
  const hex = value.replace(/[\s:.\-_]/g, "");
  if (!hex) return false;
  return !/^[0-9A-Fa-f]{12}$/.test(hex);
}

function unitsToText(units: SerialUnitForm[]) {
  return units
    .filter((u) => u.serial.trim())
    .map((u) => (u.mac.trim() ? `${u.serial.trim()},${u.mac.trim()}` : u.serial.trim()))
    .join("\n");
}

// How many units this line will create. The two entry modes count
// differently, and the VAT preview and the "N units" label both need it.
// Is this line being captured unit by unit? The line's own choice wins; with
// no choice made it falls back to how the product is set up.
function isSerialLine(line: { trackIndividually: boolean | null }, product?: Product) {
  if (line.trackIndividually !== null) return line.trackIndividually;
  return product?.tracking_type === "serialized";
}

function serialCount(line: { units: SerialUnitForm[]; serial_numbers: string; bulk: boolean }) {
  if (line.bulk) return line.serial_numbers.split("\n").filter((s) => s.trim()).length;
  return line.units.filter((u) => u.serial.trim()).length;
}

// The serial/MAC row editor, shared by "New product" (opening stock) and by
// a stock receipt line. One component so the two can't drift into behaving
// differently -- same tidying, same inline warning, same add/remove.
function SerialUnitRows({
  units,
  onChange,
  serialPlaceholder = "Serial number",
}: {
  units: SerialUnitForm[];
  onChange: (units: SerialUnitForm[]) => void;
  serialPlaceholder?: string;
}) {
  function patch(index: number, change: Partial<SerialUnitForm>) {
    onChange(units.map((u, i) => (i === index ? { ...u, ...change } : u)));
  }

  return (
    <>
      {units.map((u, i) => {
        const warn = macLooksWrong(u.mac);
        return (
          <div key={i} className="mb-2 flex items-start gap-2">
            <input
              className={inputClass}
              value={u.serial}
              placeholder={serialPlaceholder}
              onChange={(e) => patch(i, { serial: e.target.value })}
            />
            <div className="w-full">
              <input
                className={inputClass}
                value={u.mac}
                placeholder="MAC (optional)"
                onChange={(e) => patch(i, { mac: e.target.value })}
                // Tidied on blur, not per keystroke, so the caret doesn't
                // jump while someone types or a scanner fills the field.
                onBlur={(e) => patch(i, { mac: tidyMac(e.target.value) })}
              />
              {warn && (
                <p className="mt-1 text-xs text-[var(--status-critical)]">
                  Needs 12 hex digits, e.g. AA:BB:CC:DD:EE:FF
                </p>
              )}
            </div>
            <button
              type="button"
              className="mt-2 px-1 text-sm text-[var(--text-muted)] hover:text-[var(--status-critical)] disabled:opacity-30"
              disabled={units.length === 1}
              title="Remove this unit"
              onClick={() => onChange(units.filter((_, ui) => ui !== i))}
            >
              ✕
            </button>
          </div>
        );
      })}
      <button
        type="button"
        className="text-sm font-medium text-[var(--series-1)] hover:underline"
        onClick={() => onChange([...units, { ...EMPTY_SERIAL_UNIT }])}
      >
        + Add another unit
      </button>
    </>
  );
}

const EMPTY_PRODUCT: Partial<Product> = {
  name: "",
  sku: "",
  category: "other",
  tracking_type: "quantity",
  unit: "each",
  low_stock_threshold: null,
  description: "",
  is_active: true,
  sell_price: null,
  sell_tax_rate_pct: "15",
};

// Margin is a share of the SELL price; markup is a share of the COST. Buy at
// 100, sell at 150: markup 50%, margin 33.3%. The form offers markup as the
// way to SET a price (that's how a supplier price list is marked up) and
// reports margin as the result (that's what you actually keep).
function priceFromMarkup(cost: string | null, markupPct: string) {
  const c = parseFloat(cost ?? "");
  const m = parseFloat(markupPct);
  if (!Number.isFinite(c) || !Number.isFinite(m)) return null;
  return (c * (1 + m / 100)).toFixed(2);
}

// Named apart from the plain `money()` further down (which formats a number
// for the receipt totals); this one takes the API's decimal-as-string and
// renders an em dash when there isn't one.
function rand(value: string | null) {
  const n = parseFloat(value ?? "");
  return Number.isFinite(n) ? `R ${n.toFixed(2)}` : "—";
}

const PRODUCT_COLUMNS: ColumnDef[] = [
  { key: "name", label: "Name" },
  { key: "sku", label: "SKU" },
  { key: "category", label: "Category" },
  { key: "tracking", label: "Tracking" },
  { key: "cost", label: "Latest cost" },
  { key: "sell", label: "Resell price" },
  { key: "margin", label: "Margin" },
  { key: "on_hand", label: "On hand" },
  { key: "status", label: "Status" },
];

function ProductsTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const [ordering, setOrdering] = useState("name");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [trackingFilter, setTrackingFilter] = useState("");
  const [activeFilter, setActiveFilter] = useState("");
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("inventory-products", ["name"]);
  const { items, loading, refetch } = useApiList<Product>(
    `/products/?page_size=200&ordering=${ordering}${categoryFilter ? `&category=${categoryFilter}` : ""}${
      trackingFilter ? `&tracking_type=${trackingFilter}` : ""
    }${activeFilter ? `&is_active=${activeFilter}` : ""}`
  );
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState<Partial<Product>>(EMPTY_PRODUCT);
  // Opening stock entered alongside the new product. Off until asked for,
  // because most products are consumables with nothing to serialise.
  // The product being edited, or null when creating. Products had no edit
  // form at all before this -- the only way to correct a name or add a resell
  // price was to delete the product and add it again, which orphans its
  // receipt history.
  const [editingProduct, setEditingProduct] = useState<Product | null>(null);
  const [markupPct, setMarkupPct] = useState("");
  const [addUnits, setAddUnits] = useState(false);
  const [initialUnits, setInitialUnits] = useState<SerialUnitForm[]>([{ ...EMPTY_SERIAL_UNIT }]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [adjusting, setAdjusting] = useState<Product | null>(null);
  const [adjustQty, setAdjustQty] = useState("");
  const [adjustNote, setAdjustNote] = useState("");

  useEffect(() => {
    onRegisterNewAction({ label: "+ New product", onClick: () => setShowModal(true) });
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggleSort(field: string) {
    setOrdering((prev) => (prev === field ? `-${field}` : prev === `-${field}` ? "name" : field));
  }

  function closeProductModal() {
    setShowModal(false);
    setEditingProduct(null);
    setMarkupPct("");
    setForm(EMPTY_PRODUCT);
    setAddUnits(false);
    setInitialUnits([{ ...EMPTY_SERIAL_UNIT }]);
    setError("");
  }

  function openEditProduct(product: Product) {
    setEditingProduct(product);
    setForm({ ...product });
    setMarkupPct("");
    // Opening stock is a create-only idea -- adding units to a product that
    // already exists belongs on a receipt, where it gets a supplier and a cost.
    setAddUnits(false);
    setInitialUnits([{ ...EMPTY_SERIAL_UNIT }]);
    setError("");
    setShowModal(true);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      const payload: Record<string, unknown> = { ...form };
      if (editingProduct) {
        await api.patch(`/products/${editingProduct.id}/`, payload);
        closeProductModal();
        refetch();
        return;
      }
      if (addUnits) {
        // Sent as objects, not a "SERIAL,MAC" string -- the product endpoint
        // takes them structured. Rows with nothing in them are dropped here
        // as well as on the backend, so a stray empty pair is never an error.
        payload.initial_units = initialUnits
          .filter((u) => u.serial.trim() || u.mac.trim())
          .map((u) => ({ serial: u.serial.trim(), mac: u.mac.trim() }));
      }
      await api.post("/products/", payload);
      closeProductModal();
      refetch();
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const first = data ? Object.values(data).flat()[0] : null;
      setError(typeof first === "string" ? first : "Could not save this product.");
    } finally {
      setSaving(false);
    }
  }

  async function handleAdjust(e: FormEvent) {
    e.preventDefault();
    if (!adjusting) return;
    setSaving(true);
    try {
      await api.post(`/products/${adjusting.id}/adjust/`, {
        quantity: Number(adjustQty),
        note: adjustNote,
      });
      setAdjusting(null);
      setAdjustQty("");
      setAdjustNote("");
      refetch();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select className={filterSelectClass} value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
          <option value="">All categories</option>
          {Object.entries(CATEGORY_LABEL).map(([v, l]) => (
            <option key={v} value={v}>{l}</option>
          ))}
        </select>
        <select className={filterSelectClass} value={trackingFilter} onChange={(e) => setTrackingFilter(e.target.value)}>
          <option value="">All tracking types</option>
          <option value="quantity">By quantity</option>
          <option value="serialized">By serial/MAC</option>
        </select>
        <select className={filterSelectClass} value={activeFilter} onChange={(e) => setActiveFilter(e.target.value)}>
          <option value="">Active & inactive</option>
          <option value="true">Active only</option>
          <option value="false">Inactive only</option>
        </select>
        {(categoryFilter || trackingFilter || activeFilter) && (
          <button
            type="button"
            className={btnSecondary}
            onClick={() => {
              setCategoryFilter("");
              setTrackingFilter("");
              setActiveFilter("");
            }}
          >
            Clear filters
          </button>
        )}
        <div className="ml-auto">
          <ColumnToggle columns={PRODUCT_COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["name"]} />
        </div>
      </div>
      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <SortableTH field="name" ordering={ordering} onSort={toggleSort}>Name</SortableTH>
              {isVisible("sku") && <TH>SKU</TH>}
              {isVisible("category") && <SortableTH field="category" ordering={ordering} onSort={toggleSort}>Category</SortableTH>}
              {isVisible("tracking") && <SortableTH field="tracking_type" ordering={ordering} onSort={toggleSort}>Tracking</SortableTH>}
              {isVisible("cost") && <TH>Latest cost</TH>}
              {isVisible("sell") && <SortableTH field="sell_price" ordering={ordering} onSort={toggleSort}>Resell price</SortableTH>}
              {isVisible("margin") && <TH>Margin</TH>}
              {isVisible("on_hand") && <TH>On hand</TH>}
              {isVisible("status") && <TH>Status</TH>}
              <TH></TH>
            </tr>
          </THead>
          <tbody>
            {items.map((p) => (
              <TR key={p.id}>
                <TD className="font-medium">{p.name}</TD>
                {isVisible("sku") && <TD>{p.sku || "—"}</TD>}
                {isVisible("category") && <TD>{CATEGORY_LABEL[p.category]}</TD>}
                {isVisible("tracking") && <TD className="capitalize">{p.tracking_type}</TD>}
                {isVisible("cost") && <TD className="tabular-nums">{rand(p.latest_cost_excl_vat)}</TD>}
                {isVisible("sell") && (
                  <TD className="tabular-nums">
                    {p.sell_price ? (
                      rand(p.sell_price)
                    ) : (
                      <span className="text-[var(--text-muted)]">not for resale</span>
                    )}
                  </TD>
                )}
                {isVisible("margin") && (
                  <TD className="tabular-nums">
                    {p.margin_pct === null ? (
                      "—"
                    ) : (
                      <span
                        className={
                          parseFloat(p.margin_pct) < 0
                            ? "font-medium text-[var(--status-critical)]"
                            : undefined
                        }
                      >
                        {parseFloat(p.margin_pct).toFixed(1)}%
                      </span>
                    )}
                  </TD>
                )}
                {isVisible("on_hand") && (
                  <TD className="tabular-nums">
                    {p.quantity_on_hand} {p.tracking_type === "quantity" ? p.unit : ""}
                    {p.is_low_stock && (
                      <span className="ml-2 inline-block align-middle">
                        <StatusBadge status="overdue" />
                      </span>
                    )}
                  </TD>
                )}
                {isVisible("status") && (
                  <TD>
                    <StatusBadge status={p.is_active ? "active" : "inactive"} />
                  </TD>
                )}
                <TD>
                  <div className="flex gap-3">
                    <button
                      className="text-xs text-[var(--series-1)] hover:underline"
                      onClick={() => openEditProduct(p)}
                    >
                      Edit
                    </button>
                    {p.tracking_type === "quantity" && (
                      <button
                        className="text-xs text-[var(--series-1)] hover:underline"
                        onClick={() => setAdjusting(p)}
                      >
                        Adjust
                      </button>
                    )}
                  </div>
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title={editingProduct ? `Edit ${editingProduct.name}` : "New product"} onClose={closeProductModal}>
          <form onSubmit={handleSubmit}>
            <FormField label="Name">
              <input
                className={inputClass}
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </FormField>
            <FormField label="SKU (optional)">
              <input
                className={inputClass}
                value={form.sku}
                onChange={(e) => setForm({ ...form, sku: e.target.value })}
              />
            </FormField>
            <FormField label="Category">
              <select
                className={inputClass}
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value as Product["category"] })}
              >
                {Object.entries(CATEGORY_LABEL).map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
            </FormField>
            <FormField label="Tracking">
              <select
                className={inputClass}
                value={form.tracking_type}
                onChange={(e) => setForm({ ...form, tracking_type: e.target.value as Product["tracking_type"] })}
              >
                <option value="quantity">By quantity</option>
                <option value="serialized">By serial/MAC (individually tracked)</option>
              </select>
            </FormField>
            {form.tracking_type === "quantity" && (
              <>
                <FormField label="Unit">
                  <input
                    className={inputClass}
                    value={form.unit}
                    onChange={(e) => setForm({ ...form, unit: e.target.value })}
                    placeholder="each, box, meter…"
                  />
                </FormField>
                <FormField label="Low stock threshold (optional)">
                  <input
                    type="number"
                    className={inputClass}
                    value={form.low_stock_threshold ?? ""}
                    onChange={(e) =>
                      setForm({ ...form, low_stock_threshold: e.target.value ? Number(e.target.value) : null })
                    }
                  />
                </FormField>
              </>
            )}
            <FormField label="Description (optional)">
              <textarea
                className={inputClass}
                rows={2}
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
            </FormField>

            {/* Resale. Cost is never typed here -- it comes from what was
                actually paid on a receipt, and the same item bought a year
                apart costs two different amounts, so cost lives per delivery
                and only the SELL price belongs on the catalogue entry. */}
            <h3 className="mb-2 mt-4 text-sm font-semibold text-[var(--text-primary)]">Resale</h3>
            {editingProduct && (
              <p className="mb-2 text-xs text-[var(--text-muted)]">
                Latest cost {rand(editingProduct.latest_cost_excl_vat)} · weighted average{" "}
                {rand(editingProduct.average_cost_excl_vat)} (both excl. VAT, from your stock receipts)
              </p>
            )}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <FormField label="Resell price (excl. VAT)">
                <input
                  type="number"
                  step="0.01"
                  className={inputClass}
                  placeholder="Blank = not for resale"
                  value={form.sell_price ?? ""}
                  onChange={(e) => setForm({ ...form, sell_price: e.target.value || null })}
                />
              </FormField>
              <FormField label="VAT on resale (%)">
                <input
                  type="number"
                  step="0.01"
                  className={inputClass}
                  value={form.sell_tax_rate_pct ?? "15"}
                  onChange={(e) => setForm({ ...form, sell_tax_rate_pct: e.target.value })}
                />
              </FormField>
            </div>

            {editingProduct?.latest_cost_excl_vat && (
              <div className="mb-3 flex flex-wrap items-end gap-2">
                <FormField label="Or set it from cost + markup %">
                  <input
                    type="number"
                    step="0.1"
                    className={inputClass}
                    placeholder="e.g. 35"
                    value={markupPct}
                    onChange={(e) => setMarkupPct(e.target.value)}
                  />
                </FormField>
                <button
                  type="button"
                  className={btnSecondary}
                  disabled={!priceFromMarkup(editingProduct.latest_cost_excl_vat, markupPct)}
                  onClick={() =>
                    setForm({
                      ...form,
                      sell_price: priceFromMarkup(editingProduct.latest_cost_excl_vat, markupPct),
                    })
                  }
                >
                  Apply
                </button>
              </div>
            )}

            {/* Margin, not markup: they get mixed up constantly and the
                difference isn't small. Markup is the share added to cost;
                margin is the share of the sale you keep. */}
            {(() => {
              const cost = parseFloat(editingProduct?.latest_cost_excl_vat ?? "");
              const sell = parseFloat(form.sell_price ?? "");
              if (!Number.isFinite(cost) || !Number.isFinite(sell) || sell === 0) return null;
              const margin = ((sell - cost) / sell) * 100;
              const markup = ((sell - cost) / cost) * 100;
              const bad = margin < 0;
              return (
                <p
                  className={`mb-3 text-xs ${
                    bad ? "font-medium text-[var(--status-critical)]" : "text-[var(--text-muted)]"
                  }`}
                >
                  {bad ? "You'd be selling at a loss: " : ""}
                  margin <strong>{margin.toFixed(1)}%</strong> (markup {markup.toFixed(1)}%) against the latest
                  cost of {rand(editingProduct?.latest_cost_excl_vat ?? null)}.
                </p>
              );
            })()}

            <label className="mb-3 flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={form.is_active ?? true}
                onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
              />
              <span className="text-[var(--text-secondary)]">
                Active — offered when adding stock or a quote line. Unticking it keeps the history, it just stops
                appearing in the pickers.
              </span>
            </label>

            {/* Opening stock. Ticking this also switches the product to
                individually-tracked, since entering serials is the statement
                that this thing is tracked one unit at a time -- no need to
                also go and find the Tracking dropdown. */}
            {!editingProduct && (
            <label className="mb-2 flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={addUnits}
                onChange={(e) => setAddUnits(e.target.checked)}
              />
              <span className="text-[var(--text-secondary)]">
                Add units to stock now, by serial number / MAC address
              </span>
            </label>
            )}

            {addUnits && (
              <div className="mb-3 rounded-md border border-[var(--border-hairline)] bg-[var(--tint-subtle)] p-3">
                <SerialUnitRows units={initialUnits} onChange={setInitialUnits} />
                <p className="mt-2 text-xs text-[var(--text-muted)]">
                  Sets this product to individually tracked. These units go on the shelf with{" "}
                  <strong>no supplier and no cost</strong>, because there's no supplier invoice here — right for
                  opening stock or a unit that arrived loose. For an actual delivery use{" "}
                  <strong>Stock Receipts → New receipt</strong> instead, so the cost and the VAT are recorded.
                </p>
              </div>
            )}

            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={closeProductModal}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : editingProduct ? "Save changes" : "Create product"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {adjusting && (
        <Modal title={`Adjust stock — ${adjusting.name}`} onClose={() => setAdjusting(null)}>
          <form onSubmit={handleAdjust}>
            <p className="mb-3 text-sm text-[var(--text-secondary)]">
              Currently {adjusting.quantity_on_hand} {adjusting.unit} on hand.
            </p>
            <FormField label="Adjustment (positive to add, negative to remove)">
              <input
                type="number"
                className={inputClass}
                required
                value={adjustQty}
                onChange={(e) => setAdjustQty(e.target.value)}
              />
            </FormField>
            <FormField label="Reason">
              <input
                className={inputClass}
                value={adjustNote}
                onChange={(e) => setAdjustNote(e.target.value)}
                placeholder="e.g. damaged, recount…"
              />
            </FormField>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setAdjusting(null)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : "Apply"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Suppliers
// ---------------------------------------------------------------------------

const EMPTY_SUPPLIER: Partial<Supplier> = {
  name: "",
  contact_person: "",
  phone: "",
  email: "",
  address: "",
  notes: "",
  // Most suppliers Skybre buys equipment from are VAT-registered, so this
  // is the useful default. Unticking it makes their receipt lines default
  // to 0% — a supplier not registered for VAT cannot legally charge it, so
  // no Input VAT can be claimed on their invoices.
  is_vat_registered: true,
  vat_number: "",
  default_vat_rate_pct: "15.00",
  default_prices_include_vat: false,
};

const SUPPLIER_COLUMNS: ColumnDef[] = [
  { key: "name", label: "Name" },
  { key: "contact_person", label: "Contact person" },
  { key: "phone", label: "Phone" },
  { key: "email", label: "Email" },
  { key: "vat", label: "VAT" },
  { key: "prices", label: "Prices quoted" },
];

function SuppliersTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const [ordering, setOrdering] = useState("name");
  const { items, loading, refetch } = useApiList<Supplier>(`/suppliers/?page_size=200&ordering=${ordering}`);
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("inventory-suppliers", ["name"]);
  const [showModal, setShowModal] = useState(false);
  // null = creating a new supplier, an id = editing that one. The same modal
  // serves both, so the VAT fields can't drift between create and edit.
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<Partial<Supplier>>(EMPTY_SUPPLIER);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    onRegisterNewAction({
      label: "+ New supplier",
      onClick: () => {
        setEditingId(null);
        setForm(EMPTY_SUPPLIER);
        setError("");
        setShowModal(true);
      },
    });
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggleSort(field: string) {
    setOrdering((prev) => (prev === field ? `-${field}` : prev === `-${field}` ? "name" : field));
  }

  function openEdit(supplier: Supplier) {
    setEditingId(supplier.id);
    setForm({ ...supplier });
    setError("");
    setShowModal(true);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      if (editingId === null) {
        await api.post("/suppliers/", form);
      } else {
        await api.patch(`/suppliers/${editingId}/`, form);
      }
      setShowModal(false);
      setEditingId(null);
      setForm(EMPTY_SUPPLIER);
      refetch();
    } catch (err) {
      const data = (err as { response?: { data?: unknown } })?.response?.data;
      setError(
        typeof data === "string"
          ? data
          : (data as { detail?: string })?.detail || JSON.stringify(data) || "Failed to save supplier."
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div className="mb-4 flex items-center gap-2">
        <div className="ml-auto">
          <ColumnToggle columns={SUPPLIER_COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["name"]} />
        </div>
      </div>
      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <SortableTH field="name" ordering={ordering} onSort={toggleSort}>Name</SortableTH>
              {isVisible("contact_person") && <TH>Contact person</TH>}
              {isVisible("phone") && <TH>Phone</TH>}
              {isVisible("email") && <TH>Email</TH>}
              {isVisible("vat") && <TH>VAT</TH>}
              {isVisible("prices") && <TH>Prices quoted</TH>}
            </tr>
          </THead>
          <tbody>
            {items.map((s) => (
              <TR key={s.id} onClick={() => openEdit(s)}>
                <TD className="font-medium">{s.name}</TD>
                {isVisible("contact_person") && <TD>{s.contact_person || "—"}</TD>}
                {isVisible("phone") && <TD>{s.phone || "—"}</TD>}
                {isVisible("email") && <TD>{s.email || "—"}</TD>}
                {isVisible("vat") && (
                  <TD>
                    {s.is_vat_registered ? (
                      <span className="text-[var(--text-secondary)]">
                        {parseFloat(s.default_vat_rate_pct).toFixed(0)}%
                        {s.vat_number ? (
                          <span className="text-[var(--text-muted)]"> · {s.vat_number}</span>
                        ) : null}
                      </span>
                    ) : (
                      <span className="text-[var(--text-muted)]">Not registered</span>
                    )}
                  </TD>
                )}
                {isVisible("prices") && (
                  <TD>
                    {s.default_prices_include_vat ? (
                      <span className="text-[var(--text-secondary)]">Incl. VAT</span>
                    ) : (
                      <span className="text-[var(--text-muted)]">Excl. VAT</span>
                    )}
                  </TD>
                )}
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal
          title={editingId === null ? "New supplier" : `Edit ${form.name || "supplier"}`}
          onClose={() => setShowModal(false)}
        >
          <form onSubmit={handleSubmit}>
            {error && (
              <p className="mb-3 rounded-md bg-[#fbeaea] px-3 py-2 text-sm text-[#b32e2e]">{error}</p>
            )}
            <FormField label="Name">
              <input
                className={inputClass}
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </FormField>
            <FormField label="Contact person">
              <input
                className={inputClass}
                value={form.contact_person}
                onChange={(e) => setForm({ ...form, contact_person: e.target.value })}
              />
            </FormField>
            <FormField label="Phone">
              <input
                className={inputClass}
                value={form.phone}
                onChange={(e) => setForm({ ...form, phone: e.target.value })}
              />
            </FormField>
            <FormField label="Email">
              <input
                type="email"
                className={inputClass}
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />
            </FormField>
            <FormField label="Address">
              <textarea
                className={inputClass}
                rows={2}
                value={form.address}
                onChange={(e) => setForm({ ...form, address: e.target.value })}
              />
            </FormField>

            {/* VAT. This sets the DEFAULT rate on new receipt lines for this
                supplier -- the rate that counts is stored per line, so a
                zero-rated or imported item can still be recorded against a
                registered supplier. */}
            <div className="mt-4 rounded-md border border-[var(--border-hairline)] bg-[var(--surface-2)] p-3">
              <label className="flex cursor-pointer items-start gap-2.5">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={form.is_vat_registered ?? true}
                  onChange={(e) => setForm({ ...form, is_vat_registered: e.target.checked })}
                />
                <span className="text-sm">
                  <span className="font-medium text-[var(--text-primary)]">
                    Registered for VAT
                  </span>
                  <span className="block text-xs text-[var(--text-muted)]">
                    Untick for a supplier who can't charge VAT — their stock will default to 0% and no
                    Input VAT will be claimed on it.
                  </span>
                </span>
              </label>

              {form.is_vat_registered !== false && (
                <>
                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    <FormField label="Their VAT number (optional)">
                      <input
                        className={inputClass}
                        value={form.vat_number ?? ""}
                        placeholder="4123456789"
                        onChange={(e) => setForm({ ...form, vat_number: e.target.value })}
                      />
                    </FormField>
                    <FormField label="Default VAT rate (%)">
                      <input
                        type="number"
                        step="0.01"
                        min={0}
                        max={100}
                        className={inputClass}
                        value={form.default_vat_rate_pct ?? "15.00"}
                        onChange={(e) => setForm({ ...form, default_vat_rate_pct: e.target.value })}
                      />
                    </FormField>
                  </div>

                  {/* How they quote prices on their invoices. Only a default:
                      each receipt keeps its own copy, so an unusual invoice
                      can be entered the other way without editing this. */}
                  <FormField label="Their invoices quote prices">
                    <select
                      className={inputClass}
                      value={form.default_prices_include_vat ? "incl" : "excl"}
                      onChange={(e) =>
                        setForm({ ...form, default_prices_include_vat: e.target.value === "incl" })
                      }
                    >
                      <option value="excl">Excluding VAT — VAT added as a separate line</option>
                      <option value="incl">Including VAT — prices already have VAT in them</option>
                    </select>
                  </FormField>
                  <p className="-mt-2 text-xs text-[var(--text-muted)]">
                    Pre-fills the toggle when checking in their stock, so you can type prices straight
                    off the invoice. You can still change it per receipt.
                  </p>
                </>
              )}
            </div>

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : editingId === null ? "Create supplier" : "Save changes"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Units — every individually-tracked item, by serial and MAC
// ---------------------------------------------------------------------------
//
// Recording a serial and a MAC is only worth doing if you can find the unit
// again, and until now there was nowhere in the app that listed them. This is
// that list: searchable by serial, MAC or product, filterable by status,
// product and supplier, and it shows which supplier invoice each unit arrived
// on -- which is also the answer to "stock doesn't show supplier". A product
// has no single supplier (the same router can come from three over the years)
// but a physical unit has exactly one, and this is it.
//
// No "New unit" button on purpose: units come into existence by checking in a
// receipt, which is what ties them to a cost and a supplier. Creating one
// here would be stock that appeared from nowhere.

const UNIT_STATUS_LABEL: Record<SerializedUnit["status"], string> = {
  in_stock: "In stock",
  issued: "Issued",
  faulty: "Faulty",
  returned_to_supplier: "Returned to supplier",
};

const UNIT_COLUMNS: ColumnDef[] = [
  { key: "serial", label: "Serial number" },
  { key: "mac", label: "MAC address" },
  { key: "product", label: "Product" },
  { key: "status", label: "Status" },
  { key: "supplier", label: "Supplier" },
  { key: "receipt", label: "Supplier invoice" },
  { key: "received", label: "Received" },
];

function UnitsTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [productFilter, setProductFilter] = useState("");
  const [supplierFilter, setSupplierFilter] = useState("");
  const [ordering, setOrdering] = useState("serial_number");
  const [products, setProducts] = useState<Product[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const { hidden: hiddenCols, toggle: toggleCol, isVisible } = useColumnVisibility("inventory-units", ["serial"]);

  // -- correcting a unit --
  const [editing, setEditing] = useState<SerializedUnit | null>(null);
  const [editSerial, setEditSerial] = useState("");
  const [editMac, setEditMac] = useState("");
  const [editNotes, setEditNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const query = [
    "page_size=100",
    `ordering=${ordering}`,
    search.trim() ? `search=${encodeURIComponent(search.trim())}` : "",
    statusFilter ? `status=${statusFilter}` : "",
    productFilter ? `product=${productFilter}` : "",
    supplierFilter ? `supplier=${supplierFilter}` : "",
  ]
    .filter(Boolean)
    .join("&");
  const { items, loading, refetch } = useApiList<SerializedUnit>(`/serialized-units/?${query}`);

  useEffect(() => {
    onRegisterNewAction(null);
    api
      .get<{ results: Product[] }>("/products/?page_size=500&tracking_type=serialized")
      .then((r) => setProducts(r.data.results));
    api.get<{ results: Supplier[] }>("/suppliers/?page_size=200").then((r) => setSuppliers(r.data.results));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggleSort(field: string) {
    setOrdering((prev) => (prev === field ? `-${field}` : field));
  }

  function openEdit(unit: SerializedUnit) {
    setEditing(unit);
    setEditSerial(unit.serial_number);
    setEditMac(unit.mac_address);
    setEditNotes(unit.notes);
    setError("");
  }

  async function handleSave(e: FormEvent) {
    e.preventDefault();
    if (!editing) return;
    setSaving(true);
    setError("");
    try {
      await api.patch(`/serialized-units/${editing.id}/`, {
        serial_number: editSerial,
        mac_address: editMac,
        notes: editNotes,
      });
      setEditing(null);
      refetch();
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const first = data ? Object.values(data).flat()[0] : null;
      setError(typeof first === "string" ? first : "Could not save this unit.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-end gap-2">
        <input
          className={inputClass + " max-w-xs"}
          placeholder="Search serial, MAC or product…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select className={filterSelectClass} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">Any status</option>
          {(Object.keys(UNIT_STATUS_LABEL) as SerializedUnit["status"][]).map((k) => (
            <option key={k} value={k}>{UNIT_STATUS_LABEL[k]}</option>
          ))}
        </select>
        <select className={filterSelectClass} value={productFilter} onChange={(e) => setProductFilter(e.target.value)}>
          <option value="">Any product</option>
          {products.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
        <select className={filterSelectClass} value={supplierFilter} onChange={(e) => setSupplierFilter(e.target.value)}>
          <option value="">Any supplier</option>
          {suppliers.map((sup) => (
            <option key={sup.id} value={sup.id}>{sup.name}</option>
          ))}
        </select>
        {(search || statusFilter || productFilter || supplierFilter) && (
          <button
            type="button"
            className={btnSecondary}
            onClick={() => {
              setSearch("");
              setStatusFilter("");
              setProductFilter("");
              setSupplierFilter("");
            }}
          >
            Clear
          </button>
        )}
        <div className="ml-auto">
          <ColumnToggle columns={UNIT_COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["serial"]} />
        </div>
      </div>

      <p className="mb-3 text-sm text-[var(--text-muted)]">
        Every individually-tracked unit you've checked in. Click a row to correct a mis-typed serial or MAC.
      </p>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <SortableTH field="serial_number" ordering={ordering} onSort={toggleSort}>Serial number</SortableTH>
              {isVisible("mac") && <SortableTH field="mac_address" ordering={ordering} onSort={toggleSort}>MAC address</SortableTH>}
              {isVisible("product") && <TH>Product</TH>}
              {isVisible("status") && <SortableTH field="status" ordering={ordering} onSort={toggleSort}>Status</SortableTH>}
              {isVisible("supplier") && <TH>Supplier</TH>}
              {isVisible("receipt") && <TH>Supplier invoice</TH>}
              {isVisible("received") && <TH>Received</TH>}
            </tr>
          </THead>
          <tbody>
            {items.map((u) => (
              <TR key={u.id} onClick={() => openEdit(u)}>
                <TD className="font-medium">{u.serial_number}</TD>
                {isVisible("mac") && (
                  <TD className="tabular-nums">
                    {u.mac_address || <span className="text-[var(--text-muted)]">— not recorded</span>}
                  </TD>
                )}
                {isVisible("product") && <TD>{u.product_name}</TD>}
                {isVisible("status") && <TD><StatusBadge status={u.status} /></TD>}
                {isVisible("supplier") && <TD>{u.supplier_name || "—"}</TD>}
                {isVisible("receipt") && <TD>{u.receipt_invoice_number || "—"}</TD>}
                {isVisible("received") && <TD>{u.received_on || "—"}</TD>}
              </TR>
            ))}
            {items.length === 0 && (
              <TR>
                <TD className="text-[var(--text-muted)]">
                  No units match. Individually-tracked units appear here once you check them in on a stock receipt.
                </TD>
              </TR>
            )}
          </tbody>
        </Table>
      )}

      {editing && (
        <Modal title={`Unit ${editing.serial_number}`} onClose={() => setEditing(null)}>
          <form onSubmit={handleSave}>
            <p className="mb-3 text-sm text-[var(--text-secondary)]">
              {editing.product_name}
              {editing.supplier_name ? ` · from ${editing.supplier_name}` : ""}
              {editing.receipt_invoice_number ? ` · invoice ${editing.receipt_invoice_number}` : ""}
              {" · "}
              {UNIT_STATUS_LABEL[editing.status]}
            </p>
            <FormField label="Serial number">
              <input className={inputClass} value={editSerial} onChange={(e) => setEditSerial(e.target.value)} />
            </FormField>
            <FormField label="MAC address">
              <input
                className={inputClass}
                value={editMac}
                placeholder="AA:BB:CC:DD:EE:FF"
                onChange={(e) => setEditMac(e.target.value)}
                onBlur={(e) => setEditMac(tidyMac(e.target.value))}
              />
            </FormField>
            {macLooksWrong(editMac) && (
              <p className="-mt-2 mb-3 text-xs text-[var(--status-critical)]">
                Needs 12 hex digits, e.g. AA:BB:CC:DD:EE:FF
              </p>
            )}
            <FormField label="Notes">
              <textarea className={inputClass} rows={2} value={editNotes} onChange={(e) => setEditNotes(e.target.value)} />
            </FormField>
            <p className="mb-3 text-xs text-[var(--text-muted)]">
              Which product this is and whether it's issued are set by the receipt and issue flows, so they can't be
              changed here — that keeps the on-hand count honest.
            </p>
            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setEditing(null)}>Cancel</button>
              <button type="submit" className={btnPrimary} disabled={saving}>
                {saving ? "Saving…" : "Save changes"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stock Receipts (check-in against a supplier invoice)
// ---------------------------------------------------------------------------

type ReceiptLineForm = {
  product: string;
  quantity: string;
  // null = "however this product is set up" (the old behaviour). true/false
  // is an explicit choice made on this line, which the backend will apply to
  // the product too -- so nobody has to know the Tracking dropdown exists.
  trackIndividually: boolean | null;
  // Structured entry (the default) and the pasted-list escape hatch. Only
  // one of the two is sent, decided by `bulk`.
  units: SerialUnitForm[];
  serial_numbers: string;
  bulk: boolean;
  unit_cost: string;
  // Empty string means "use the supplier's default", which the backend
  // fills in. An explicit "0" is a deliberate zero-rated line and is NOT
  // replaced by the default — that distinction is what makes zero-rated
  // and imported items work against a VAT-registered supplier.
  vat_rate_pct: string;
};
const EMPTY_RECEIPT_LINE: ReceiptLineForm = {
  product: "",
  quantity: "1",
  trackIndividually: null,
  units: [{ ...EMPTY_SERIAL_UNIT }],
  serial_numbers: "",
  bulk: false,
  unit_cost: "",
  vat_rate_pct: "",
};

const RECEIPT_COLUMNS: ColumnDef[] = [
  { key: "date", label: "Date" },
  { key: "invoice_number", label: "Invoice #" },
  { key: "supplier", label: "Supplier" },
  { key: "lines", label: "Lines" },
  { key: "total", label: "Total" },
  { key: "vat", label: "VAT" },
  { key: "received_by", label: "Received by" },
  { key: "attachment", label: "Attachment" },
];

// Mirrors StockReceipt.totals / StockReceiptLine on the backend so the form
// can show live figures before saving. The backend remains authoritative --
// this exists only so staff can check the total against the invoice in their
// hand before committing, which is where entry errors actually get caught.
function previewLineVat(unitCost: string, qty: number, ratePct: string, pricesIncludeVat: boolean) {
  const cost = parseFloat(unitCost);
  if (!isFinite(cost) || qty <= 0) return { excl: 0, vat: 0, incl: 0 };
  const total = cost * qty;
  const rate = parseFloat(ratePct);
  if (!isFinite(rate)) return { excl: total, vat: 0, incl: total };
  if (pricesIncludeVat) {
    const excl = total / (1 + rate / 100);
    return { excl, vat: total - excl, incl: total };
  }
  const vat = total * (rate / 100);
  return { excl: total, vat, incl: total + vat };
}

function money(value: number) {
  return value.toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function ReceiptsTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const [ordering, setOrdering] = useState("-invoice_date");
  const [supplierFilter, setSupplierFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("inventory-receipts", ["invoice_number"]);
  const { items, loading, refetch } = useApiList<StockReceipt>(
    `/stock-receipts/?page_size=100&ordering=${ordering}${supplierFilter ? `&supplier=${supplierFilter}` : ""}${
      dateFrom ? `&date_from=${dateFrom}` : ""
    }${dateTo ? `&date_to=${dateTo}` : ""}`
  );
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [viewing, setViewing] = useState<StockReceipt | null>(null);
  const [supplier, setSupplier] = useState("");
  const [invoiceNumber, setInvoiceNumber] = useState("");
  const [invoiceDate, setInvoiceDate] = useState("");
  const [notes, setNotes] = useState("");
  const [pricesIncludeVat, setPricesIncludeVat] = useState(false);
  const [attachment, setAttachment] = useState<File | null>(null);
  // Shown on the page, not in the modal: the modal closes when this fires,
  // because the receipt itself did save and re-submitting it would create
  // a duplicate. The warning has to outlive the form that produced it.
  const [attachmentWarning, setAttachmentWarning] = useState("");
  // Attaching (or replacing) the invoice on a receipt that already exists.
  const [lateAttachment, setLateAttachment] = useState<File | null>(null);
  const [lateSaving, setLateSaving] = useState(false);
  const [lateError, setLateError] = useState("");
  const [lines, setLines] = useState<ReceiptLineForm[]>([{ ...EMPTY_RECEIPT_LINE }]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const selectedSupplier = suppliers.find((s) => String(s.id) === supplier);
  // What a line with a blank rate will actually be saved as. Shown as the
  // placeholder so the default is visible rather than implied.
  const defaultRate = selectedSupplier
    ? parseFloat(selectedSupplier.effective_vat_rate_pct)
    : null;

  const previewTotals = lines.reduce(
    (acc, l) => {
      const product = productFor(l.product);
      const qty = isSerialLine(l, product) ? serialCount(l) : Number(l.quantity) || 0;
      const rate = l.vat_rate_pct !== "" ? l.vat_rate_pct : String(defaultRate ?? "");
      const r = previewLineVat(l.unit_cost, qty, rate, pricesIncludeVat);
      return { excl: acc.excl + r.excl, vat: acc.vat + r.vat, incl: acc.incl + r.incl };
    },
    { excl: 0, vat: 0, incl: 0 }
  );

  useEffect(() => {
    api.get<{ results: Supplier[] }>("/suppliers/?page_size=200").then((r) => setSuppliers(r.data.results));
    api
      .get<{ results: Product[] }>("/products/?page_size=500&is_active=true")
      .then((r) => setProducts(r.data.results));
  }, []);

  useEffect(() => {
    onRegisterNewAction({ label: "+ New receipt", onClick: () => setShowModal(true) });
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggleSort(field: string) {
    setOrdering((prev) => (prev === field ? `-${field}` : prev === `-${field}` ? "-invoice_date" : field));
  }

  function productFor(id: string) {
    return products.find((p) => String(p.id) === id);
  }

  function updateLine(index: number, patch: Partial<ReceiptLineForm>) {
    setLines((prev) => prev.map((l, i) => (i === index ? { ...l, ...patch } : l)));
  }

  function addLine() {
    setLines((prev) => [...prev, { ...EMPTY_RECEIPT_LINE }]);
  }

  function removeLine(index: number) {
    setLines((prev) => prev.filter((_, i) => i !== index));
  }

  function resetForm() {
    setSupplier("");
    setInvoiceNumber("");
    setInvoiceDate("");
    setNotes("");
    setPricesIncludeVat(false);
    setAttachment(null);
    setLines([{ ...EMPTY_RECEIPT_LINE }]);
    setError("");
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      const payload = {
        supplier: Number(supplier),
        invoice_number: invoiceNumber,
        invoice_date: invoiceDate,
        notes,
        prices_include_vat: pricesIncludeVat,
        lines: lines.map((l) => {
          const product = productFor(l.product);
          const base: Record<string, unknown> = { product: Number(l.product), unit_cost: l.unit_cost || null };
          // Only send a rate if one was actually typed. Omitting the key
          // lets the backend apply the supplier's default; sending "0"
          // records a deliberate zero-rated line. `!== ""` rather than a
          // truthiness check, because "0" is falsy but meaningful.
          if (l.vat_rate_pct !== "") {
            base.vat_rate_pct = l.vat_rate_pct;
          }
          if (l.trackIndividually !== null) {
            base.track_individually = l.trackIndividually;
          }
          if (isSerialLine(l, product)) {
            // Both modes collapse to the same wire format -- one unit per
            // line, "SERIAL" or "SERIAL,MAC" -- which is what the backend
            // parses and validates.
            base.serial_numbers = l.bulk ? l.serial_numbers : unitsToText(l.units);
          } else {
            base.quantity = Number(l.quantity) || 0;
          }
          return base;
        }),
      };
      const res = await api.post<StockReceipt>("/stock-receipts/", payload);
      if (attachment) {
        // Its own try/catch, because by this point THE RECEIPT ALREADY
        // EXISTS. Letting a failed upload fall into the outer catch would
        // report "Failed to save receipt", which is untrue and sends
        // somebody off to create the whole thing a second time.
        try {
          const fd = new FormData();
          fd.append("attachment", attachment);
          // Content-Type is left to the browser on purpose -- setting
          // multipart/form-data by hand omits the boundary parameter,
          // which some proxies reject outright.
          const saved = await api.patch<StockReceipt>(`/stock-receipts/${res.data.id}/`, fd);
          if (!saved.data.attachment) {
            // Belt and braces: a 200 that came back with no attachment
            // means the field was accepted and dropped, which is exactly
            // how this failed silently before. Never treat that as a win
            // again.
            throw new Error("The server accepted the file but didn't store it.");
          }
        } catch (uploadErr) {
          const data = (uploadErr as { response?: { data?: { attachment?: string[] } } })?.response?.data;
          const why = data?.attachment?.[0] || (uploadErr as Error).message || "The upload failed.";
          setShowModal(false);
          resetForm();
          refetch();
          setAttachmentWarning(
            `Receipt ${payload.invoice_number} was saved, but the invoice file was not attached — ${why} Open the receipt to attach it.`
          );
          return;
        }
      }
      setShowModal(false);
      resetForm();
      refetch();
      setAttachmentWarning("");
    } catch (err) {
      const detail = (err as { response?: { data?: unknown } })?.response?.data as
        | { detail?: string }
        | string
        | undefined;
      setError(
        typeof detail === "string" ? detail : detail?.detail || JSON.stringify(detail) || "Failed to save receipt."
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleAttachToExisting() {
    if (!viewing || !lateAttachment) return;
    setLateSaving(true);
    setLateError("");
    try {
      const fd = new FormData();
      fd.append("attachment", lateAttachment);
      const saved = await api.patch<StockReceipt>(`/stock-receipts/${viewing.id}/`, fd);
      if (!saved.data.attachment) throw new Error("The server accepted the file but didn't store it.");
      setViewing(saved.data);
      setLateAttachment(null);
      setAttachmentWarning("");
      refetch();
    } catch (err) {
      const data = (err as { response?: { data?: { attachment?: string[] } } })?.response?.data;
      setLateError(data?.attachment?.[0] || (err as Error).message || "The upload failed.");
    } finally {
      setLateSaving(false);
    }
  }

  return (
    <div>
      {/* Dismissible rather than auto-clearing: it reports a receipt that
          exists WITHOUT its invoice attached, which is a thing somebody
          has to go and finish, not a toast to blink past. */}
      {attachmentWarning && (
        <div className="mb-4 flex items-start justify-between gap-3 rounded-md border border-[var(--status-warning)]/50 bg-[var(--status-warning)]/10 px-3 py-2 text-sm text-[var(--text-secondary)]">
          <span>{attachmentWarning}</span>
          <button
            type="button"
            aria-label="Dismiss"
            className="text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            onClick={() => setAttachmentWarning("")}
          >
            ✕
          </button>
        </div>
      )}
      <div className="mb-4 flex flex-wrap items-end gap-2">
        <select className={filterSelectClass} value={supplierFilter} onChange={(e) => setSupplierFilter(e.target.value)}>
          <option value="">All suppliers</option>
          {suppliers.map((s) => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
        <FormField label="Invoice date from">
          <input type="date" className={inputClass} value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        </FormField>
        <FormField label="Invoice date to">
          <input type="date" className={inputClass} value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        </FormField>
        {(supplierFilter || dateFrom || dateTo) && (
          <button
            type="button"
            className={`${btnSecondary} mb-3`}
            onClick={() => {
              setSupplierFilter("");
              setDateFrom("");
              setDateTo("");
            }}
          >
            Clear filters
          </button>
        )}
        <div className="ml-auto mb-3">
          <ColumnToggle columns={RECEIPT_COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["invoice_number"]} />
        </div>
      </div>
      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              {isVisible("date") && <SortableTH field="invoice_date" ordering={ordering} onSort={toggleSort}>Date</SortableTH>}
              <TH>Invoice #</TH>
              {isVisible("supplier") && <SortableTH field="supplier__name" ordering={ordering} onSort={toggleSort}>Supplier</SortableTH>}
              {isVisible("lines") && <TH>Lines</TH>}
              {isVisible("total") && <TH>Total incl.</TH>}
              {isVisible("vat") && <TH>VAT</TH>}
              {isVisible("received_by") && <TH>Received by</TH>}
              {isVisible("attachment") && <TH>Attachment</TH>}
            </tr>
          </THead>
          <tbody>
            {items.map((r) => (
              <TR key={r.id} onClick={() => setViewing(r)}>
                {isVisible("date") && <TD>{r.invoice_date}</TD>}
                <TD className="font-medium">{r.invoice_number}</TD>
                {isVisible("supplier") && <TD>{r.supplier_name}</TD>}
                {isVisible("lines") && <TD>{r.lines.length}</TD>}
                {isVisible("total") && (
                  <TD className="tabular-nums">R {money(parseFloat(r.total_incl_vat))}</TD>
                )}
                {isVisible("vat") && (
                  <TD className="tabular-nums">
                    R {money(parseFloat(r.vat_total))}
                    {r.has_unrecorded_vat && (
                      <span
                        className="ml-1.5 text-[var(--text-muted)]"
                        title="One or more lines were captured before VAT tracking existed, so they claim no VAT. This figure may be understated."
                      >
                        ⚠
                      </span>
                    )}
                  </TD>
                )}
                {isVisible("received_by") && <TD>{r.received_by_name || "—"}</TD>}
                {isVisible("attachment") && <TD>{r.attachment ? "Yes" : "—"}</TD>}
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title="New stock receipt" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            {error && (
              <p className="mb-3 rounded-md bg-[#fbeaea] px-3 py-2 text-sm text-[#b32e2e]">{error}</p>
            )}
            <FormField label="Supplier">
              <select
                className={inputClass}
                required
                value={supplier}
                onChange={(e) => {
                  const id = e.target.value;
                  setSupplier(id);
                  // Adopt this supplier's usual convention. Deliberately
                  // overwrites any earlier choice: changing supplier means
                  // a different invoice, so carrying the old setting over
                  // would be the more surprising behaviour. It stays
                  // editable below.
                  const picked = suppliers.find((s) => String(s.id) === id);
                  if (picked) setPricesIncludeVat(picked.default_prices_include_vat);
                }}
              >
                <option value="">Select a supplier…</option>
                {suppliers.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </FormField>
            <FormField label="Supplier invoice number">
              <input
                className={inputClass}
                required
                value={invoiceNumber}
                onChange={(e) => setInvoiceNumber(e.target.value)}
              />
            </FormField>
            <FormField label="Invoice date">
              <input
                type="date"
                className={inputClass}
                required
                value={invoiceDate}
                onChange={(e) => setInvoiceDate(e.target.value)}
              />
            </FormField>
            <FormField label="Attachment (optional — photo/PDF of the invoice)">
              <input
                type="file"
                className={inputClass}
                onChange={(e) => setAttachment(e.target.files?.[0] ?? null)}
              />
            </FormField>

            {/* One toggle for the whole receipt, because a supplier invoice
                quotes prices one way throughout. The VAT *rate* stays per
                line below, so a mixed standard/zero-rated invoice works. */}
            <div className="mt-4 rounded-md border border-[var(--border-hairline)] bg-[var(--surface-2)] p-3">
              <label className="flex cursor-pointer items-start gap-2.5">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={pricesIncludeVat}
                  onChange={(e) => setPricesIncludeVat(e.target.checked)}
                />
                <span className="text-sm">
                  <span className="font-medium text-[var(--text-primary)]">
                    Unit costs below include VAT
                  </span>
                  <span className="block text-xs text-[var(--text-muted)]">
                    Tick this and type the prices exactly as they appear on a VAT-inclusive invoice —
                    no need to work backwards. Leave it unticked for VAT-exclusive prices.
                    {selectedSupplier && (
                      <>
                        {" "}Pre-set from {selectedSupplier.name}'s usual convention; change it if this
                        invoice differs.
                      </>
                    )}
                  </span>
                </span>
              </label>
              {selectedSupplier && (
                <p className="mt-2 border-t border-[var(--border-hairline)] pt-2 text-xs text-[var(--text-muted)]">
                  {selectedSupplier.is_vat_registered ? (
                    <>
                      <span className="font-medium text-[var(--text-secondary)]">
                        {selectedSupplier.name}
                      </span>{" "}
                      is VAT registered — lines default to{" "}
                      <span className="font-medium text-[var(--text-secondary)]">
                        {defaultRate}%
                      </span>
                      . Override any line below for zero-rated or imported items.
                    </>
                  ) : (
                    <>
                      <span className="font-medium text-[var(--text-secondary)]">
                        {selectedSupplier.name}
                      </span>{" "}
                      is not VAT registered, so lines default to 0% and no VAT can be claimed. Change
                      this on the supplier if that's wrong.
                    </>
                  )}
                </p>
              )}
            </div>

            <div className="mb-2 mt-4 text-sm font-medium text-[var(--text-secondary)]">Items received</div>
            {lines.map((line, i) => {
              const product = productFor(line.product);
              return (
                <div key={i} className="mb-3 rounded-md border border-[var(--border-hairline)] p-3">
                  <FormField label="Product">
                    <select
                      className={inputClass}
                      required
                      value={line.product}
                      onChange={(e) => updateLine(i, { product: e.target.value })}
                    >
                      <option value="">Select a product…</option>
                      {products.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                  </FormField>
                  {/* The switch, so serial/MAC entry is available right
                      here rather than depending on a Tracking setting made
                      back on the Products tab. */}
                  {product && (
                    <label className="mb-2 flex items-start gap-2 text-sm">
                      <input
                        type="checkbox"
                        className="mt-1"
                        checked={isSerialLine(line, product)}
                        onChange={(e) => updateLine(i, { trackIndividually: e.target.checked })}
                      />
                      <span className="text-[var(--text-secondary)]">
                        Record these individually, by serial number / MAC address
                      </span>
                    </label>
                  )}

                  {isSerialLine(line, product) ? (
                    <div className="mb-3">
                      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
                        <span className="text-sm font-medium text-[var(--text-secondary)]">
                          Units — serial number and MAC address
                        </span>
                        <button
                          type="button"
                          className="text-xs font-medium text-[var(--series-1)] hover:underline"
                          onClick={() =>
                            updateLine(i, {
                              bulk: !line.bulk,
                              // Switching mode carries whatever is already
                              // typed across, so nothing is lost.
                              serial_numbers: line.bulk ? line.serial_numbers : unitsToText(line.units),
                            })
                          }
                        >
                          {line.bulk ? "Enter one by one" : "Paste a list instead"}
                        </button>
                      </div>

                      {line.bulk ? (
                        <>
                          <textarea
                            className={inputClass}
                            rows={4}
                            value={line.serial_numbers}
                            onChange={(e) => updateLine(i, { serial_numbers: e.target.value })}
                            placeholder={"SN12345,AA:BB:CC:DD:EE:FF\nSN12346"}
                          />
                          <p className="mt-1 text-xs text-[var(--text-muted)]">
                            One unit per line, as <code>SERIAL</code> or <code>SERIAL,MAC</code>. For pasting a
                            column out of a supplier's spreadsheet.
                          </p>
                        </>
                      ) : (
                        <>
                          <SerialUnitRows
                            units={line.units}
                            onChange={(units) => updateLine(i, { units })}
                          />
                          <p className="mt-2 text-xs text-[var(--text-muted)]">
                            {serialCount(line)} unit{serialCount(line) === 1 ? "" : "s"} on this line · MAC is
                            optional, but it's what lets you find the unit later.
                          </p>
                        </>
                      )}
                    </div>
                  ) : (
                    <FormField label={`Quantity${product ? ` (${product.unit})` : ""}`}>
                      <input
                        type="number"
                        min={1}
                        className={inputClass}
                        value={line.quantity}
                        onChange={(e) => updateLine(i, { quantity: e.target.value })}
                      />
                    </FormField>
                  )}
                  <div className="grid gap-3 sm:grid-cols-2">
                    <FormField
                      label={`Unit cost (optional, R ${pricesIncludeVat ? "incl. VAT" : "excl. VAT"})`}
                    >
                      <input
                        type="number"
                        step="0.01"
                        className={inputClass}
                        value={line.unit_cost}
                        onChange={(e) => updateLine(i, { unit_cost: e.target.value })}
                      />
                    </FormField>
                    <FormField label="VAT rate (%)">
                      <input
                        type="number"
                        step="0.01"
                        min={0}
                        max={100}
                        className={inputClass}
                        value={line.vat_rate_pct}
                        placeholder={defaultRate !== null ? `${defaultRate} (supplier default)` : "Select a supplier"}
                        onChange={(e) => updateLine(i, { vat_rate_pct: e.target.value })}
                      />
                    </FormField>
                  </div>
                  {/* Live per-line breakdown. Cheap to render and it's what
                      lets someone catch a mistyped cost against the invoice
                      in their hand, before it reaches the VAT return. */}
                  {(() => {
                    const qty =
                      product?.tracking_type === "serialized"
                        ? line.serial_numbers.split("\n").filter((s) => s.trim()).length
                        : Number(line.quantity) || 0;
                    const rate = line.vat_rate_pct !== "" ? line.vat_rate_pct : String(defaultRate ?? "");
                    const r = previewLineVat(line.unit_cost, qty, rate, pricesIncludeVat);
                    if (r.incl === 0) return null;
                    return (
                      <p className="mb-2 text-xs tabular-nums text-[var(--text-muted)]">
                        {qty} × R {money(parseFloat(line.unit_cost) || 0)} = R {money(r.excl)} excl. + R{" "}
                        {money(r.vat)} VAT = <span className="font-medium">R {money(r.incl)}</span>
                      </p>
                    );
                  })()}
                  {lines.length > 1 && (
                    <button
                      type="button"
                      className="text-xs text-[#b32e2e] hover:underline"
                      onClick={() => removeLine(i)}
                    >
                      Remove line
                    </button>
                  )}
                </div>
              );
            })}
            <button type="button" className={`${btnSecondary} mb-3`} onClick={addLine}>
              + Add another item
            </button>

            {/* Receipt total, so it can be checked against the supplier's
                own invoice total before saving. The backend recalculates
                and remains authoritative -- this is a sanity check, and it
                is the step that catches entry errors. */}
            {previewTotals.incl > 0 && (
              <div className="mb-3 rounded-md border border-[var(--border-hairline)] bg-[var(--surface-2)] p-3">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-[var(--text-secondary)]">Subtotal excl. VAT</span>
                  <span className="tabular-nums">R {money(previewTotals.excl)}</span>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span className="text-[var(--text-secondary)]">VAT</span>
                  <span className="tabular-nums">R {money(previewTotals.vat)}</span>
                </div>
                <div className="mt-1 flex items-center justify-between border-t border-[var(--border-hairline)] pt-1 text-sm font-semibold">
                  <span>Invoice total</span>
                  <span className="tabular-nums">R {money(previewTotals.incl)}</span>
                </div>
                <p className="mt-2 text-xs text-[var(--text-muted)]">
                  Check the total against the supplier's invoice before saving. The VAT above is what
                  gets claimed as Input VAT on your next VAT return.
                </p>
              </div>
            )}

            <FormField label="Notes (optional)">
              <textarea className={inputClass} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
            </FormField>

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : "Check in stock"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {viewing && (
        <Modal title={`Receipt ${viewing.invoice_number}`} onClose={() => setViewing(null)}>
          <p className="mb-1 text-sm">
            <span className="font-medium">Supplier:</span> {viewing.supplier_name}
          </p>
          <p className="mb-1 text-sm">
            <span className="font-medium">Date:</span> {viewing.invoice_date}
          </p>
          {viewing.received_by_name && (
            <p className="mb-1 text-sm">
              <span className="font-medium">Received by:</span> {viewing.received_by_name}
            </p>
          )}
          {viewing.notes && (
            <p className="mb-1 text-sm">
              <span className="font-medium">Notes:</span> {viewing.notes}
            </p>
          )}
          <div className="mb-3 rounded-md border border-[var(--border-hairline)] p-3">
            {viewing.attachment ? (
              <p className="text-sm">
                <a
                  href={viewing.attachment}
                  target="_blank"
                  rel="noreferrer"
                  className="text-[var(--series-1)] hover:underline"
                >
                  View attached invoice
                </a>
              </p>
            ) : (
              <p className="text-sm text-[var(--text-muted)]">No invoice attached.</p>
            )}
            {/* Attaching AFTER the fact, because a receipt can outlive a
                failed upload and re-creating the whole thing to fix one
                missing file is not a reasonable remedy. */}
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <input
                type="file"
                className="text-xs text-[var(--text-secondary)]"
                accept=".pdf,.png,.jpg,.jpeg,.webp,.heic,.gif,.txt"
                onChange={(e) => {
                  setLateAttachment(e.target.files?.[0] ?? null);
                  setLateError("");
                }}
              />
              <button
                type="button"
                className={btnSecondary}
                disabled={!lateAttachment || lateSaving}
                onClick={handleAttachToExisting}
              >
                {lateSaving ? "Uploading…" : viewing.attachment ? "Replace" : "Attach"}
              </button>
            </div>
            <p className="mt-1 text-xs text-[var(--text-muted)]">
              PDF or a photo, up to 15 MB.
            </p>
            {lateError && (
              <p className="mt-1 text-xs text-[var(--status-critical)]">{lateError}</p>
            )}
          </div>
          <p className="mb-3 text-sm">
            <span className="font-medium">Prices entered:</span>{" "}
            {viewing.prices_include_vat ? "including VAT" : "excluding VAT"}
          </p>
          <div className="mt-3 space-y-2">
            {viewing.lines.map((l) => (
              <div key={l.id} className="rounded-md border border-[var(--border-hairline)] p-2 text-sm">
                <div className="font-medium">{l.product_name}</div>
                {l.serial_numbers ? (
                  <div className="whitespace-pre-line text-xs text-[var(--text-secondary)]">
                    {l.serial_numbers}
                  </div>
                ) : (
                  <div className="text-xs text-[var(--text-secondary)]">
                    Qty: {l.quantity}
                    {l.unit_cost ? ` · R ${parseFloat(l.unit_cost).toFixed(2)} each` : ""}
                  </div>
                )}
                {l.unit_cost && (
                  <div className="mt-1 text-xs tabular-nums text-[var(--text-muted)]">
                    {l.vat_recorded ? (
                      <>
                        R {money(parseFloat(l.line_excl_vat))} excl. + R {money(parseFloat(l.line_vat))} VAT
                        {" @ "}
                        {parseFloat(l.vat_rate_pct ?? "0").toFixed(2).replace(/\.00$/, "")}% = R{" "}
                        {money(parseFloat(l.line_incl_vat))}
                      </>
                    ) : (
                      <span title="Captured before VAT tracking existed — no VAT is claimed on this line.">
                        R {money(parseFloat(l.line_incl_vat))} · VAT not recorded
                      </span>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Same figures the VAT return uses for this receipt. */}
          <div className="mt-3 rounded-md border border-[var(--border-hairline)] bg-[var(--surface-2)] p-3">
            <div className="flex items-center justify-between text-sm">
              <span className="text-[var(--text-secondary)]">Subtotal excl. VAT</span>
              <span className="tabular-nums">R {money(parseFloat(viewing.total_excl_vat))}</span>
            </div>
            <div className="flex items-center justify-between text-sm">
              <span className="text-[var(--text-secondary)]">Input VAT claimable</span>
              <span className="tabular-nums">R {money(parseFloat(viewing.vat_total))}</span>
            </div>
            <div className="mt-1 flex items-center justify-between border-t border-[var(--border-hairline)] pt-1 text-sm font-semibold">
              <span>Invoice total</span>
              <span className="tabular-nums">R {money(parseFloat(viewing.total_incl_vat))}</span>
            </div>
            {viewing.has_unrecorded_vat && (
              <p className="mt-2 text-xs text-[#b3852e]">
                Some lines were captured before VAT tracking existed. They claim no VAT, so the figure
                above may be understated — edit those lines to record a rate if it matters.
              </p>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stock Issues (issued out to jobs / technicians)
// ---------------------------------------------------------------------------

type IssueLineForm = { product: string; quantity: string; serial_unit: string };
const EMPTY_ISSUE_LINE: IssueLineForm = { product: "", quantity: "1", serial_unit: "" };

const ISSUE_COLUMNS: ColumnDef[] = [
  { key: "date", label: "Date" },
  { key: "job", label: "Job / Customer" },
  { key: "issued_to", label: "Issued to" },
  { key: "items", label: "Items" },
];

function IssuesTab({ onRegisterNewAction }: { onRegisterNewAction: (action: NewAction) => void }) {
  const [ordering, setOrdering] = useState("-issued_at");
  const [issuedToFilter, setIssuedToFilter] = useState("");
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("inventory-issues", ["date"]);
  const { items, loading, refetch } = useApiList<StockIssue>(
    `/stock-issues/?page_size=100&ordering=${ordering}${issuedToFilter ? `&issued_to=${issuedToFilter}` : ""}`
  );
  const [products, setProducts] = useState<Product[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [staff, setStaff] = useState<User[]>([]);
  const [availableUnits, setAvailableUnits] = useState<Record<string, SerializedUnit[]>>({});
  const [showModal, setShowModal] = useState(false);
  const [viewing, setViewing] = useState<StockIssue | null>(null);
  const [job, setJob] = useState("");
  const [issuedTo, setIssuedTo] = useState("");
  const [notes, setNotes] = useState("");
  const [lines, setLines] = useState<IssueLineForm[]>([{ ...EMPTY_ISSUE_LINE }]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .get<{ results: Product[] }>("/products/?page_size=500&is_active=true")
      .then((r) => setProducts(r.data.results));
    api.get<{ results: Job[] }>("/jobs/?page_size=200&ordering=-start").then((r) => setJobs(r.data.results));
    api.get<{ results: User[] }>("/staff-users/?page_size=100").then((r) => setStaff(r.data.results));
  }, []);

  useEffect(() => {
    onRegisterNewAction({ label: "+ New issue", onClick: () => setShowModal(true) });
    return () => onRegisterNewAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggleSort(field: string) {
    setOrdering((prev) => (prev === field ? `-${field}` : prev === `-${field}` ? "-issued_at" : field));
  }

  function productFor(id: string) {
    return products.find((p) => String(p.id) === id);
  }

  async function loadUnitsFor(productId: string) {
    if (availableUnits[productId]) return;
    const res = await api.get<{ results: SerializedUnit[] }>(
      `/serialized-units/?product=${productId}&status=in_stock&page_size=200`
    );
    setAvailableUnits((prev) => ({ ...prev, [productId]: res.data.results }));
  }

  function updateLine(index: number, patch: Partial<IssueLineForm>) {
    setLines((prev) => prev.map((l, i) => (i === index ? { ...l, ...patch } : l)));
  }

  async function handleProductChange(index: number, productId: string) {
    updateLine(index, { product: productId, serial_unit: "" });
    const product = productFor(productId);
    if (product?.tracking_type === "serialized") {
      await loadUnitsFor(productId);
    }
  }

  function addLine() {
    setLines((prev) => [...prev, { ...EMPTY_ISSUE_LINE }]);
  }

  function removeLine(index: number) {
    setLines((prev) => prev.filter((_, i) => i !== index));
  }

  function resetForm() {
    setJob("");
    setIssuedTo("");
    setNotes("");
    setLines([{ ...EMPTY_ISSUE_LINE }]);
    setError("");
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      const payload = {
        job: job ? Number(job) : null,
        issued_to: issuedTo ? Number(issuedTo) : null,
        notes,
        lines: lines.map((l) => {
          const product = productFor(l.product);
          if (product?.tracking_type === "serialized") {
            const unit = availableUnits[l.product]?.find((u) => String(u.id) === l.serial_unit);
            return { product: Number(l.product), serial_number: unit?.serial_number || "" };
          }
          return { product: Number(l.product), quantity: Number(l.quantity) || 0 };
        }),
      };
      await api.post("/stock-issues/", payload);
      setShowModal(false);
      resetForm();
      refetch();
    } catch (err) {
      const detail = (err as { response?: { data?: unknown } })?.response?.data as
        | { detail?: string }
        | string
        | undefined;
      setError(
        typeof detail === "string" ? detail : detail?.detail || JSON.stringify(detail) || "Failed to save issue."
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select className={filterSelectClass} value={issuedToFilter} onChange={(e) => setIssuedToFilter(e.target.value)}>
          <option value="">All staff</option>
          {staff.map((s) => (
            <option key={s.id} value={s.id}>{s.first_name || s.username}</option>
          ))}
        </select>
        {issuedToFilter && (
          <button type="button" className={btnSecondary} onClick={() => setIssuedToFilter("")}>
            Clear filters
          </button>
        )}
        <div className="ml-auto">
          <ColumnToggle columns={ISSUE_COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["date"]} />
        </div>
      </div>
      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <SortableTH field="issued_at" ordering={ordering} onSort={toggleSort}>Date</SortableTH>
              {isVisible("job") && <TH>Job / Customer</TH>}
              {isVisible("issued_to") && <SortableTH field="issued_to__username" ordering={ordering} onSort={toggleSort}>Issued to</SortableTH>}
              {isVisible("items") && <TH>Items</TH>}
            </tr>
          </THead>
          <tbody>
            {items.map((iss) => (
              <TR key={iss.id} onClick={() => setViewing(iss)}>
                <TD>{new Date(iss.issued_at).toLocaleString()}</TD>
                {isVisible("job") && (
                  <TD>
                    {iss.job_title
                      ? `${iss.job_title}${iss.customer_name ? ` (${iss.customer_name})` : ""}`
                      : "Standalone"}
                  </TD>
                )}
                {isVisible("issued_to") && <TD>{iss.issued_to_name || "—"}</TD>}
                {isVisible("items") && <TD>{iss.lines.length}</TD>}
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title="New stock issue" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            {error && (
              <p className="mb-3 rounded-md bg-[#fbeaea] px-3 py-2 text-sm text-[#b32e2e]">{error}</p>
            )}
            <FormField label="Job (optional — leave blank for standalone)">
              <select className={inputClass} value={job} onChange={(e) => setJob(e.target.value)}>
                <option value="">No job (standalone)</option>
                {jobs.map((j) => (
                  <option key={j.id} value={j.id}>
                    {j.title}
                    {j.customer_name ? ` — ${j.customer_name}` : ""}
                  </option>
                ))}
              </select>
            </FormField>
            <FormField label="Issued to (technician/staff)">
              <select className={inputClass} value={issuedTo} onChange={(e) => setIssuedTo(e.target.value)}>
                <option value="">Select staff…</option>
                {staff.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.username}
                  </option>
                ))}
              </select>
            </FormField>

            <div className="mb-2 mt-4 text-sm font-medium text-[var(--text-secondary)]">Items issued</div>
            {lines.map((line, i) => {
              const product = productFor(line.product);
              const units = availableUnits[line.product] || [];
              return (
                <div key={i} className="mb-3 rounded-md border border-[var(--border-hairline)] p-3">
                  <FormField label="Product">
                    <select
                      className={inputClass}
                      required
                      value={line.product}
                      onChange={(e) => handleProductChange(i, e.target.value)}
                    >
                      <option value="">Select a product…</option>
                      {products.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name} ({p.quantity_on_hand} {p.tracking_type === "quantity" ? p.unit : "in stock"})
                        </option>
                      ))}
                    </select>
                  </FormField>
                  {product?.tracking_type === "serialized" ? (
                    <FormField label="Serial number">
                      <select
                        className={inputClass}
                        required
                        value={line.serial_unit}
                        onChange={(e) => updateLine(i, { serial_unit: e.target.value })}
                      >
                        <option value="">{units.length ? "Select a unit…" : "Loading…"}</option>
                        {units.map((u) => (
                          <option key={u.id} value={u.id}>
                            {u.serial_number}
                            {u.mac_address ? ` (${u.mac_address})` : ""}
                          </option>
                        ))}
                      </select>
                    </FormField>
                  ) : (
                    <FormField
                      label={`Quantity${
                        product ? ` (${product.unit}, ${product.quantity_on_hand} available)` : ""
                      }`}
                    >
                      <input
                        type="number"
                        min={1}
                        className={inputClass}
                        value={line.quantity}
                        onChange={(e) => updateLine(i, { quantity: e.target.value })}
                      />
                    </FormField>
                  )}
                  {lines.length > 1 && (
                    <button
                      type="button"
                      className="text-xs text-[#b32e2e] hover:underline"
                      onClick={() => removeLine(i)}
                    >
                      Remove line
                    </button>
                  )}
                </div>
              );
            })}
            <button type="button" className={`${btnSecondary} mb-3`} onClick={addLine}>
              + Add another item
            </button>

            <FormField label="Notes (optional)">
              <textarea className={inputClass} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
            </FormField>

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : "Issue stock"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {viewing && (
        <Modal title={`Issue #${viewing.id}`} onClose={() => setViewing(null)}>
          <p className="mb-1 text-sm">
            <span className="font-medium">Job:</span> {viewing.job_title || "Standalone"}
          </p>
          {viewing.customer_name && (
            <p className="mb-1 text-sm">
              <span className="font-medium">Customer:</span> {viewing.customer_name}
            </p>
          )}
          <p className="mb-1 text-sm">
            <span className="font-medium">Issued to:</span> {viewing.issued_to_name || "—"}
          </p>
          <p className="mb-1 text-sm">
            <span className="font-medium">Date:</span> {new Date(viewing.issued_at).toLocaleString()}
          </p>
          {viewing.notes && (
            <p className="mb-3 text-sm">
              <span className="font-medium">Notes:</span> {viewing.notes}
            </p>
          )}
          <div className="mt-3 space-y-2">
            {viewing.lines.map((l) => (
              <div key={l.id} className="rounded-md border border-[var(--border-hairline)] p-2 text-sm">
                <div className="font-medium">{l.product_name}</div>
                <div className="text-xs text-[var(--text-secondary)]">
                  {l.serial_number
                    ? `Serial: ${l.serial_number}${l.mac_address ? ` (${l.mac_address})` : ""}`
                    : `Qty: ${l.quantity}`}
                </div>
              </div>
            ))}
          </div>
        </Modal>
      )}
    </div>
  );
}
