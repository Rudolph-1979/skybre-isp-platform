import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Modal } from "./Modal";

/** Line of sight from a tower to a house over terrain (#179). */

export type Los = {
  available: boolean;
  reason?: string;
  verdict?: "clear" | "marginal" | "blocked";
  detail?: string;
  distance_m?: number;
  tower_ground_m?: number;
  tower_height_m?: number;
  client_ground_m?: number;
  client_height_m?: number;
  freq_mhz?: number;
  profile?: { d: number; ground: number; sight: number; fresnel_low: number }[];
};

export const LOS_COLOUR: Record<string, string> = {
  clear: "#22c55e",
  marginal: "#eda100",
  blocked: "#e34948",
};
export const LOS_LABEL: Record<string, string> = {
  clear: "Line of sight clear",
  marginal: "Line of sight marginal",
  blocked: "Line of sight blocked",
};

export function LosBadge({ los }: { los?: Los }) {
  if (!los) return null;
  if (!los.available) return <span className="text-[11px] text-[var(--text-muted)]">Line of sight: {los.reason}</span>;
  const c = LOS_COLOUR[los.verdict!];
  return (
    <span className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-semibold" style={{ color: c, background: "var(--tint-hover)" }}>
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: c }} />
      {LOS_LABEL[los.verdict!]}
    </span>
  );
}

export function LosProfileModal({ title, los, onClose }: { title: string; los: Los; onClose: () => void }) {
  const data = (los.profile ?? []).map((p) => ({ ...p, km: p.d / 1000 }));
  const lo = Math.min(...data.map((p) => Math.min(p.ground, p.fresnel_low))) - 5;
  const hi = Math.max(...data.map((p) => Math.max(p.ground, p.sight))) + 5;
  return (
    <Modal title={title} onClose={onClose}>
      <p className="mb-1 text-sm font-semibold" style={{ color: LOS_COLOUR[los.verdict!] }}>
        {LOS_LABEL[los.verdict!]}
      </p>
      <p className="mb-3 text-xs text-[var(--text-secondary)]">{los.detail}</p>
      <ResponsiveContainer width="100%" height={220}>
        <ComposedChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--border-hairline)" vertical={false} />
          <XAxis dataKey="km" type="number" domain={[0, "dataMax"]} tickFormatter={(v) => `${Number(v).toFixed(1)} km`} tick={{ fontSize: 10, fill: "var(--text-muted)" }} />
          <YAxis domain={[Math.floor(lo), Math.ceil(hi)]} tickFormatter={(v) => `${v} m`} tick={{ fontSize: 10, fill: "var(--text-muted)" }} width={56} />
          <Tooltip
            formatter={(v, n) => [`${Number(v).toFixed(0)} m`, String(n)]}
            labelFormatter={(v) => `${Number(v).toFixed(2)} km from the tower`}
            contentStyle={{ background: "var(--surface-1)", border: "1px solid var(--baseline)", fontSize: 12 }}
          />
          <Area dataKey="ground" name="Ground (+ earth curve)" type="monotone" stroke="#8b6b4a" fill="#8b6b4a" fillOpacity={0.45} baseValue={Math.floor(lo)} isAnimationActive={false} />
          <Line dataKey="fresnel_low" name="60% Fresnel edge" type="monotone" stroke="#2a78d6" strokeDasharray="4 4" dot={false} strokeWidth={1} isAnimationActive={false} />
          <Line dataKey="sight" name="Sight line" type="linear" stroke="#2a78d6" dot={false} strokeWidth={2} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
      <div className="mt-2 grid grid-cols-2 gap-x-4 text-xs text-[var(--text-secondary)]">
        <span>Tower: ground {los.tower_ground_m} m + antenna {los.tower_height_m} m (UISP site height)</span>
        <span>House: ground {los.client_ground_m} m + antenna {los.client_height_m} m</span>
        <span>Distance {((los.distance_m ?? 0) / 1000).toFixed(2)} km</span>
        <span>Frequency {los.freq_mhz} MHz</span>
      </div>
      <p className="mt-2 text-[11px] text-[var(--text-muted)]">
        Terrain: Copernicus 30 m surface model, so hills and large buildings/tree belts show; single trees and the neighbour's roof
        don't. Confirm with a scan before promising the install.
      </p>
    </Modal>
  );
}
