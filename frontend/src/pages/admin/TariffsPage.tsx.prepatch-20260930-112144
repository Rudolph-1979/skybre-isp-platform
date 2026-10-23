import { useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, SortableTH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, filterSelectClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { CSVImportModal } from "../../components/CSVImportModal";
import { ColumnToggle, type ColumnDef } from "../../components/ColumnToggle";
import { useColumnVisibility } from "../../hooks/useColumnVisibility";
import type { Tariff } from "../../types";

const COLUMNS: ColumnDef[] = [
  { key: "name", label: "Name" },
  { key: "type", label: "Type" },
  { key: "speed", label: "Speed" },
  { key: "price", label: "Price" },
  { key: "billing_period", label: "Billing period" },
  { key: "status", label: "Status" },
];

const IMPORT_TEMPLATE_HEADERS = [
  "name", "service_type", "price", "billing_period",
  "speed_download_kbps", "speed_upload_kbps", "data_cap_gb",
  "tax_rate_pct", "is_active", "description",
];

const EMPTY: Partial<Tariff> = {
  name: "",
  service_type: "internet",
  price: "",
  billing_period: "monthly",
  speed_download_kbps: null,
  speed_upload_kbps: null,
  tax_rate_pct: "15",
  is_active: true,
};

export function TariffsPage() {
  const [ordering, setOrdering] = useState("name");
  const [typeFilter, setTypeFilter] = useState("");
  const [activeFilter, setActiveFilter] = useState("");
  const { hidden: hiddenCols, isVisible, toggle: toggleCol } = useColumnVisibility("tariffs", ["name"]);
  const { items, loading, refetch } = useApiList<Tariff>(
    `/tariffs/?page_size=100&ordering=${ordering}${typeFilter ? `&service_type=${typeFilter}` : ""}${
      activeFilter ? `&is_active=${activeFilter}` : ""
    }`
  );
  const [showModal, setShowModal] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [form, setForm] = useState<Partial<Tariff>>(EMPTY);
  const [saving, setSaving] = useState(false);

  function toggleSort(field: string) {
    setOrdering((prev) => (prev === field ? `-${field}` : prev === `-${field}` ? "name" : field));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await api.post("/tariffs/", form);
      setShowModal(false);
      setForm(EMPTY);
      refetch();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Tariffs & Plans"
        subtitle="Internet, voice, and bundle packages offered to customers."
        actions={
          <>
            <button className={btnSecondary} onClick={() => setShowImport(true)}>
              Import CSV
            </button>
            <button className={btnPrimary} onClick={() => setShowModal(true)}>
              + New tariff
            </button>
          </>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select className={filterSelectClass} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          <option value="">All types</option>
          <option value="internet">Internet</option>
          <option value="voice">Voice</option>
          <option value="bundle">Bundle</option>
          <option value="other">Other</option>
        </select>
        <select className={filterSelectClass} value={activeFilter} onChange={(e) => setActiveFilter(e.target.value)}>
          <option value="">Active & inactive</option>
          <option value="true">Active only</option>
          <option value="false">Inactive only</option>
        </select>
        {(typeFilter || activeFilter) && (
          <button
            type="button"
            className={btnSecondary}
            onClick={() => {
              setTypeFilter("");
              setActiveFilter("");
            }}
          >
            Clear filters
          </button>
        )}
        <div className="ml-auto">
          <ColumnToggle columns={COLUMNS} hidden={hiddenCols} onToggle={toggleCol} alwaysVisible={["name"]} />
        </div>
      </div>

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <SortableTH field="name" ordering={ordering} onSort={toggleSort}>Name</SortableTH>
              {isVisible("type") && <SortableTH field="service_type" ordering={ordering} onSort={toggleSort}>Type</SortableTH>}
              {isVisible("speed") && <SortableTH field="speed_download_kbps" ordering={ordering} onSort={toggleSort}>Speed</SortableTH>}
              {isVisible("price") && <SortableTH field="price" ordering={ordering} onSort={toggleSort}>Price</SortableTH>}
              {isVisible("billing_period") && <SortableTH field="billing_period" ordering={ordering} onSort={toggleSort}>Billing period</SortableTH>}
              {isVisible("status") && <SortableTH field="is_active" ordering={ordering} onSort={toggleSort}>Status</SortableTH>}
            </tr>
          </THead>
          <tbody>
            {items.map((t) => (
              <TR key={t.id}>
                <TD className="font-medium">{t.name}</TD>
                {isVisible("type") && <TD className="capitalize">{t.service_type}</TD>}
                {isVisible("speed") && <TD>{t.speed_download_kbps ? `${t.speed_download_kbps}/${t.speed_upload_kbps} Kbps` : "—"}</TD>}
                {isVisible("price") && <TD className="tabular-nums">R {parseFloat(t.price).toFixed(2)}</TD>}
                {isVisible("billing_period") && <TD className="capitalize">{t.billing_period}</TD>}
                {isVisible("status") && <TD><StatusBadge status={t.is_active ? "active" : "inactive"} /></TD>}
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      {showModal && (
        <Modal title="New tariff" onClose={() => setShowModal(false)}>
          <form onSubmit={handleSubmit}>
            <FormField label="Name">
              <input className={inputClass} required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </FormField>
            <FormField label="Service type">
              <select
                className={inputClass}
                value={form.service_type}
                onChange={(e) => setForm({ ...form, service_type: e.target.value as Tariff["service_type"] })}
              >
                <option value="internet">Internet</option>
                <option value="voice">Voice</option>
                <option value="bundle">Bundle</option>
                <option value="other">Other</option>
              </select>
            </FormField>
            <FormField label="Price (R)">
              <input
                type="number"
                step="0.01"
                className={inputClass}
                required
                value={form.price}
                onChange={(e) => setForm({ ...form, price: e.target.value })}
              />
            </FormField>
            <FormField label="Download speed (Kbps)">
              <input
                type="number"
                className={inputClass}
                value={form.speed_download_kbps ?? ""}
                onChange={(e) => setForm({ ...form, speed_download_kbps: e.target.value ? Number(e.target.value) : null })}
              />
            </FormField>
            <FormField label="Upload speed (Kbps)">
              <input
                type="number"
                className={inputClass}
                value={form.speed_upload_kbps ?? ""}
                onChange={(e) => setForm({ ...form, speed_upload_kbps: e.target.value ? Number(e.target.value) : null })}
              />
            </FormField>
            {/* Said out loud because leaving these blank does NOT fail. The
                rate limit falls back to 10 Mbps, so every customer on the
                plan silently gets a speed nobody chose, at whatever price
                this tariff charges. Only for internet plans — a voice or
                other tariff having no speed is normal. */}
            {form.service_type === "internet" &&
              (!form.speed_download_kbps || !form.speed_upload_kbps) && (
                <div className="sm:col-span-2">
                  <p className="rounded-md border border-[var(--status-warning)] bg-[#fff6e5] p-2 text-xs text-[#a5730a]">
                    No speed set. Saving like this doesn't fail — it hands everyone on this plan a
                    fallback of <strong>10240 Kbps (10 Mbps)</strong> regardless of what they pay.
                    1 Mbps is 1024, so a 4 Mbps plan is 4096.
                  </p>
                </div>
              )}
            <FormField label="Billing period">
              <select
                className={inputClass}
                value={form.billing_period}
                onChange={(e) => setForm({ ...form, billing_period: e.target.value as Tariff["billing_period"] })}
              >
                <option value="monthly">Monthly</option>
                <option value="quarterly">Quarterly</option>
                <option value="annually">Annually</option>
              </select>
            </FormField>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : "Create tariff"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {showImport && (
        <CSVImportModal
          title="Import tariffs"
          importUrlBase="/tariffs/"
          templateHeaders={IMPORT_TEMPLATE_HEADERS}
          templateFilename="tariffs_template.csv"
          onClose={() => setShowImport(false)}
          onImported={refetch}
        />
      )}
    </div>
  );
}
