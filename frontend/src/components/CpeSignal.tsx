import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Modal, btnSecondary } from "./Modal";
import { formatIsoDateTime } from "../utils/billingTerms";
import { apiErrorMessage } from "../utils/apiError";

export type CpeSignalReading = {
  service: number;
  state: "ok" | "not_seen" | "no_mac";
  mac: string;
  ap: number | null;
  ap_name: string | null;
  interface: string;
  radio_name: string;
  signal_dbm: number | null;
  tx_signal_dbm: number | null;
  snr_db: number | null;
  tx_ccq: number | null;
  rx_ccq: number | null;
  tx_rate: string;
  rx_rate: string;
  uptime: string;
  distance_km: string;
  seen_at: string | null;
  checked_at: string | null;
};

type Sample = { at: string; signal_dbm: number | null; tx_signal_dbm: number | null; tx_ccq: number | null };

/** Green better than -65 dBm, amber to -75, red below. */
export function signalTone(dbm: number | null | undefined) {
  if (dbm == null) return "var(--text-muted)";
  if (dbm >= -65) return "var(--status-good, #22c55e)";
  if (dbm >= -75) return "var(--status-warning)";
  return "var(--status-critical)";
}

function dbm(v: number | null | undefined) {
  return v == null ? "—" : `${v}`;
}

/** Services table cell (8 Oct 2026). A line never seen on a MikroTik AP --
 *  fibre lines (Skybre resells those, so there is no optical level to read),
 *  or a CPE on an AP the platform can't read -- shows a dash, not an alarm. */
export function CpeSignalCell({ reading, onOpen }: { reading?: CpeSignalReading; onOpen: () => void }) {
  if (!reading || !reading.seen_at) {
    return (
      <span
        className="text-[var(--text-muted)]"
        title="Not seen on any MikroTik AP the platform reads — e.g. a fibre line, or an AP not added under Networking → Devices with the API on."
      >
        —
      </span>
    );
  }
  const stale = reading.state !== "ok";
  return (
    <button type="button" className="text-left hover:underline" onClick={onOpen}>
      <span className="whitespace-nowrap font-medium" style={{ color: stale ? "var(--text-muted)" : signalTone(reading.signal_dbm) }}>
        {dbm(reading.tx_signal_dbm)} / {dbm(reading.signal_dbm)} dBm
      </span>
      <span className="block whitespace-nowrap text-xs text-[var(--text-muted)]">
        {stale
          ? `Not seen now · last ${formatIsoDateTime(reading.seen_at)}`
          : `CCQ ${reading.tx_ccq ?? "—"}/${reading.rx_ccq ?? "—"} · ${reading.ap_name ?? ""}`}
      </span>
    </button>
  );
}

function Graph({ samples }: { samples: Sample[] }) {
  const pts = samples.filter((s) => s.signal_dbm != null);
  if (pts.length < 2) return <p className="text-sm text-[var(--text-muted)]">Not enough readings yet — one every 5 minutes.</p>;
  const w = 640;
  const h = 140;
  const lo = Math.min(-90, ...pts.map((p) => p.signal_dbm!), ...pts.map((p) => p.tx_signal_dbm ?? 0).filter((v) => v < 0));
  const hi = -30;
  const t0 = Date.parse(pts[0].at);
  const t1 = Date.parse(pts[pts.length - 1].at) || t0 + 1;
  const x = (at: string) => ((Date.parse(at) - t0) / Math.max(1, t1 - t0)) * w;
  const y = (v: number) => ((hi - v) / (hi - lo)) * h;
  const line = (key: "signal_dbm" | "tx_signal_dbm") =>
    pts
      .filter((p) => p[key] != null)
      .map((p, i) => `${i ? "L" : "M"}${x(p.at).toFixed(1)},${y(p[key]!).toFixed(1)}`)
      .join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h + 16}`} width="100%" role="img" aria-label="Signal over time">
      {[-50, -65, -75].map((g) => (
        <g key={g}>
          <line x1={0} x2={w} y1={y(g)} y2={y(g)} stroke="var(--border-hairline)" />
          <text x={w - 2} y={y(g) - 2} fontSize="10" textAnchor="end" fill="var(--text-muted)">
            {g} dBm
          </text>
        </g>
      ))}
      <path d={line("signal_dbm")} fill="none" stroke="var(--series-1)" strokeWidth={1.6} />
      <path d={line("tx_signal_dbm")} fill="none" stroke="#c58af9" strokeWidth={1.2} strokeDasharray="4 3" />
      <text x={0} y={h + 13} fontSize="10" fill="var(--text-muted)">
        {formatIsoDateTime(pts[0].at)}
      </text>
      <text x={w} y={h + 13} fontSize="10" textAnchor="end" fill="var(--text-muted)">
        {formatIsoDateTime(pts[pts.length - 1].at)}
      </text>
    </svg>
  );
}

/** Details, graph and Refresh for one line. */
export function CpeSignalModal({
  serviceId,
  title,
  initial,
  onClose,
  onUpdated,
}: {
  serviceId: number;
  title: string;
  initial?: CpeSignalReading;
  onClose: () => void;
  onUpdated: (r: CpeSignalReading) => void;
}) {
  const [reading, setReading] = useState<CpeSignalReading | undefined>(initial);
  const [hours, setHours] = useState(24);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .get<{ results: Sample[] }>(`/cpe-signals/${serviceId}/history/?hours=${hours}`)
      .then((r) => setSamples(r.data.results))
      .catch(() => setSamples([]));
  }, [serviceId, hours, reading?.checked_at]);

  async function refresh() {
    setBusy(true);
    setError("");
    try {
      const r = await api.post<CpeSignalReading>(`/cpe-signals/${serviceId}/refresh/`);
      setReading(r.data);
      onUpdated(r.data);
    } catch (err) {
      setError(apiErrorMessage(err, "Couldn't read the AP."));
    } finally {
      setBusy(false);
    }
  }

  const r = reading;
  const fact = (label: string, value: string | number | null | undefined) => (
    <div>
      <span className="block text-xs text-[var(--text-muted)]">{label}</span>
      <span className="break-words text-sm text-[var(--text-primary)]">{value == null || value === "" ? "—" : value}</span>
    </div>
  );
  return (
    <Modal title={`Signal — ${title}`} onClose={onClose}>
      {!r || !r.seen_at ? (
        <p className="text-sm text-[var(--text-secondary)]">
          {r?.state === "no_mac"
            ? "No CPE MAC from RADIUS yet — it is recorded the next time this line logs in."
            : `CPE ${r?.mac || ""} isn't on any MikroTik AP the platform reads.`}
        </p>
      ) : (
        <>
          <div className="mb-3 flex items-baseline gap-3">
            <span className="text-2xl font-semibold" style={{ color: signalTone(r.signal_dbm) }}>
              {dbm(r.tx_signal_dbm)} / {dbm(r.signal_dbm)} dBm
            </span>
            <span className="text-xs text-[var(--text-muted)]">at the CPE / at the AP</span>
          </div>
          {r.state !== "ok" && (
            <p className="mb-3 text-sm text-[var(--status-warning)]">
              Not on the AP at the last check ({r.checked_at ? formatIsoDateTime(r.checked_at) : "—"}) — figures are from{" "}
              {formatIsoDateTime(r.seen_at)}.
            </p>
          )}
          <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
            {fact("AP", r.ap_name ? `${r.ap_name} · ${r.interface}` : null)}
            {fact("Radio name", r.radio_name)}
            {fact("CCQ tx / rx", r.tx_ccq == null ? null : `${r.tx_ccq}% / ${r.rx_ccq ?? "—"}%`)}
            {fact("SNR", r.snr_db == null ? null : `${r.snr_db} dB`)}
            {fact("Tx rate", r.tx_rate)}
            {fact("Rx rate", r.rx_rate)}
            {fact("Connected", r.uptime)}
            {fact("Distance", r.distance_km ? `${r.distance_km} km` : null)}
            {fact("CPE MAC", r.mac)}
            {fact("Last read", r.checked_at ? formatIsoDateTime(r.checked_at) : null)}
          </div>
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-medium">
              Signal{" "}
              <span className="text-xs font-normal text-[var(--text-muted)]">— solid: at the AP · dashed: at the CPE</span>
            </span>
            <span className="flex gap-1">
              {[24, 72, 168].map((hh) => (
                <button
                  key={hh}
                  type="button"
                  onClick={() => setHours(hh)}
                  className={`rounded px-2 py-0.5 text-xs ${hours === hh ? "bg-[var(--series-1)] text-white" : "text-[var(--text-muted)]"}`}
                >
                  {hh === 24 ? "24 h" : hh === 72 ? "3 d" : "7 d"}
                </button>
              ))}
            </span>
          </div>
          <Graph samples={samples} />
        </>
      )}
      {error && <p className="mt-2 text-sm text-[var(--status-critical)]">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" className={btnSecondary} onClick={refresh} disabled={busy}>
          {busy ? "Reading the AP…" : "Refresh now"}
        </button>
        <button type="button" className={btnSecondary} onClick={onClose}>
          Close
        </button>
      </div>
    </Modal>
  );
}
