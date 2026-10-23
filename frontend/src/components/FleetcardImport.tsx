import { useState } from "react";
import { api } from "../api/client";
import { Modal, btnPrimary, btnSecondary } from "./Modal";
import { StatusBadge } from "./StatusBadge";
import { apiErrorMessage } from "../utils/apiError";
import { formatIsoDate } from "../utils/billingTerms";

/**
 * Import the FNB Fleetcard statement file into the fuel log (#201, Rudolph
 * 19 Oct 2026). Choose the file -> see every fill-up and what will happen to
 * it -> Import. Importing the same file again adds nothing; a fill-up already
 * captured by hand or in Conecto is not doubled.
 */

type Row = {
  line: number;
  registration: string;
  vehicle_id: number | null;
  vehicle_name: string;
  date: string;
  time: string;
  garage: string;
  litres: string;
  amount: string;
  odometer_km: number;
  status: "new" | "added" | "imported" | "captured" | "no_vehicle" | "reversal";
  note: string;
};
type Result = {
  client: string;
  statement_date: string | null;
  committed: boolean;
  created: number;
  odometer_readings: number;
  counts: Record<string, number>;
  errors: string[];
  rows: Row[];
};

const STATUS: Record<Row["status"], { badge: string; label: string }> = {
  new: { badge: "new", label: "Will be added" },
  added: { badge: "approved", label: "Added" },
  imported: { badge: "closed", label: "Already imported" },
  captured: { badge: "closed", label: "Already captured" },
  no_vehicle: { badge: "pending", label: "Vehicle not in Vexar" },
  reversal: { badge: "pending", label: "Reversal — skipped" },
};

function rands(v: string) {
  return `R${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function FleetcardImportModal({ onClose, onImported }: { onClose: () => void; onImported: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function send(f: File, commit: boolean) {
    setBusy(true);
    setError("");
    try {
      const body = new FormData();
      body.append("file", f);
      if (commit) body.append("commit", "1");
      const r = await api.post<Result>("/fuel-logs/import-fleetcard/", body);
      setResult(r.data);
      if (commit) onImported();
    } catch (err) {
      setError(apiErrorMessage(err, "Couldn't read that file."));
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  const toAdd = result?.counts.new ?? 0;

  return (
    <Modal title="Import FNB Fleetcard fill-ups" onClose={onClose}>
      <p className="mb-3 text-sm text-[var(--text-secondary)]">
        Choose the Fleetcard statement file from FNB (e.g. <code>84389.txt</code>). Each fill-up goes into the vehicle&rsquo;s fuel log
        (litres, amount, garage, odometer). Nothing is saved until you click Import.
      </p>
      <input
        type="file"
        accept=".txt,.dat,text/plain"
        className="mb-3 block w-full text-sm text-[var(--text-secondary)]"
        onChange={(e) => {
          const f = e.target.files?.[0] ?? null;
          setFile(f);
          setResult(null);
          if (f) send(f, false);
        }}
      />
      {busy && <p className="text-sm text-[var(--text-muted)]">Reading…</p>}
      {error && <p className="text-sm text-[var(--status-critical)]">{error}</p>}

      {result && (
        <div className="space-y-3">
          <p className="text-sm">
            Fleetcard account {result.client}
            {result.statement_date ? ` · statement ${formatIsoDate(result.statement_date)}` : ""} · {result.rows.length} fill-up
            {result.rows.length === 1 ? "" : "s"}
          </p>
          {result.committed && (
            <p className="rounded-md bg-[var(--tint-subtle)] p-2 text-sm text-[var(--status-good)]">
              {result.created} fill-up{result.created === 1 ? "" : "s"} added to the fuel log, {result.odometer_readings} odometer
              reading{result.odometer_readings === 1 ? "" : "s"} added.
            </p>
          )}
          {result.errors.length > 0 && (
            <ul className="text-xs text-[var(--status-warning)]">
              {result.errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}
          <ul className="max-h-[45vh] divide-y divide-[var(--border-hairline)] overflow-y-auto rounded-md border border-[var(--border-hairline)] text-sm">
            {result.rows.map((r) => (
              <li key={`${r.line}`} className="px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">
                    {r.registration}
                    <span className="ml-2 text-xs font-normal text-[var(--text-muted)]">
                      {formatIsoDate(r.date)} {r.time}
                    </span>
                  </span>
                  <StatusBadge status={STATUS[r.status].badge} label={STATUS[r.status].label} />
                </div>
                <div className="text-xs text-[var(--text-secondary)] tabular-nums">
                  {Number(r.litres).toLocaleString()} L · {rands(r.amount)} · {r.odometer_km.toLocaleString()} km · {r.garage}
                </div>
                {r.note && r.status !== "new" && <div className="text-xs text-[var(--text-muted)]">{r.note}</div>}
              </li>
            ))}
          </ul>
          <div className="flex justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={onClose}>
              {result.committed ? "Close" : "Cancel"}
            </button>
            {!result.committed && (
              <button type="button" className={btnPrimary} disabled={busy || !file || toAdd === 0} onClick={() => file && send(file, true)}>
                {toAdd ? `Import ${toAdd} fill-up${toAdd === 1 ? "" : "s"}` : "Nothing new to import"}
              </button>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}
