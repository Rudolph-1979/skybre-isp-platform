import { useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { useApiList } from "../../hooks/useApiList";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { CSVImportModal } from "../../components/CSVImportModal";
import type { Tariff } from "../../types";

const IMPORT_TEMPLATE_HEADERS = [
  "name", "service_type", "price", "billing_period",
  "speed_download_mbps", "speed_upload_mbps", "data_cap_gb",
  "tax_rate_pct", "is_active", "description",
];

const EMPTY: Partial<Tariff> = {
  name: "",
  service_type: "internet",
  price: "",
  billing_period: "monthly",
  speed_download_mbps: null,
  speed_upload_mbps: null,
  tax_rate_pct: "15",
  is_active: true,
};

export function TariffsPage() {
  const { items, loading, refetch } = useApiList<Tariff>("/tariffs/?page_size=100");
  const [showModal, setShowModal] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [form, setForm] = useState<Partial<Tariff>>(EMPTY);
  const [saving, setSaving] = useState(false);

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

      {loading ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Name</TH>
              <TH>Type</TH>
              <TH>Speed</TH>
              <TH>Price</TH>
              <TH>Billing period</TH>
              <TH>Status</TH>
            </tr>
          </THead>
          <tbody>
            {items.map((t) => (
              <TR key={t.id}>
                <TD className="font-medium">{t.name}</TD>
                <TD className="capitalize">{t.service_type}</TD>
                <TD>{t.speed_download_mbps ? `${t.speed_download_mbps}/${t.speed_upload_mbps} Mbps` : "—"}</TD>
                <TD className="tabular-nums">R {parseFloat(t.price).toFixed(2)}</TD>
                <TD className="capitalize">{t.billing_period}</TD>
                <TD><StatusBadge status={t.is_active ? "active" : "inactive"} /></TD>
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
            <FormField label="Download speed (Mbps)">
              <input
                type="number"
                className={inputClass}
                value={form.speed_download_mbps ?? ""}
                onChange={(e) => setForm({ ...form, speed_download_mbps: e.target.value ? Number(e.target.value) : null })}
              />
            </FormField>
            <FormField label="Upload speed (Mbps)">
              <input
                type="number"
                className={inputClass}
                value={form.speed_upload_mbps ?? ""}
                onChange={(e) => setForm({ ...form, speed_upload_mbps: e.target.value ? Number(e.target.value) : null })}
              />
            </FormField>
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
