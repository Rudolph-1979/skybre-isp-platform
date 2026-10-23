import { useMemo, useState } from "react";

/**
 * Topology drawn like UISP's own map (#172, Rudolph 14 Oct 2026): Internet on
 * the left, then each site in a column by how many hops it is from the core,
 * joined by its backhaul link. A line is the real radio pair (a station and
 * the AP it connects to, from UISP), coloured by that link's health: red =
 * a radio down, amber = busy or low link potential, green = fine, grey dashed
 * = UISP links the sites but no radio pair was found. The % on a line is the
 * link's downlink use. Click a site for its devices, a device for its graphs.
 */

export type MapSite = {
  id: string;
  name: string;
  parent_id: string | null;
  device_count: number;
  devices_offline: number;
  clients: number | null;
};
export type MapDevice = {
  id: string;
  name: string;
  model: string;
  kind: string;
  online: boolean;
  maintenance: boolean;
  black_box: boolean;
  ip: string;
  site: { id: string; name: string } | null;
  flags: string[];
  link_potential_pct: number | null;
  downlink_use_pct: number | null;
  ap_device_id?: string | null;
  ap_site_id?: string | null;
};

const BOX_W = 156;
const BOX_H = 44;
const COL_GAP = 50;
const ROW_GAP = 14;
const PAD = 16;
const NET_W = 70;

const GOOD = "var(--status-good, #22c55e)";
const WARN = "var(--status-warning)";
const BAD = "var(--status-critical)";
const NONE = "var(--text-muted)";

type Placed = { site: MapSite; depth: number; x: number; y: number };
type Edge = {
  from: Placed | null; // null = Internet
  to: Placed;
  colour: string;
  dashed: boolean;
  label: string;
  title: string;
};

function linkHealth(radios: MapDevice[]) {
  if (radios.some((r) => !r.online)) return BAD;
  if (radios.some((r) => r.flags.includes("busy") || r.flags.includes("low_link"))) return WARN;
  return GOOD;
}

export function UispTopologyMap({ sites, devices, onPick }: { sites: MapSite[]; devices: MapDevice[]; onPick: (d: MapDevice) => void }) {
  const [selected, setSelected] = useState<string | null>(null);

  const layout = useMemo(() => {
    const ids = new Set(sites.map((s) => s.id));
    const kids: Record<string, MapSite[]> = {};
    for (const s of sites) {
      const parent = s.parent_id && ids.has(s.parent_id) ? s.parent_id : "root";
      (kids[parent] ??= []).push(s);
    }
    Object.values(kids).forEach((l) => l.sort((a, b) => a.name.localeCompare(b.name)));
    // Roots with nothing under them and no radio link are stand-alone sites
    // (UISP leaves them off its map too); they're listed under the map.
    const linkedSites = new Set(devices.filter((d) => d.ap_site_id && d.site).flatMap((d) => [d.ap_site_id!, d.site!.id]));
    const roots = (kids["root"] ?? []).filter((r) => (kids[r.id]?.length ?? 0) > 0 || linkedSites.has(r.id));
    const loose = (kids["root"] ?? []).filter((r) => !roots.includes(r));

    // Columns by depth; within a column, in the order of the parents (so
    // lines cross as little as possible), then by name.
    const columns: MapSite[][] = [];
    let level = roots;
    let depth = 0;
    while (level.length) {
      columns[depth] = level;
      level = level.flatMap((s) => kids[s.id] ?? []);
      depth += 1;
    }
    const tallest = Math.max(1, ...columns.map((c) => c.length));
    const height = PAD * 2 + tallest * BOX_H + (tallest - 1) * ROW_GAP;
    const placed: Record<string, Placed> = {};
    columns.forEach((col, d) => {
      const colH = col.length * BOX_H + (col.length - 1) * ROW_GAP;
      const top = (height - colH) / 2;
      col.forEach((s, i) => {
        placed[s.id] = { site: s, depth: d, x: PAD + NET_W + COL_GAP + d * (BOX_W + COL_GAP), y: top + i * (BOX_H + ROW_GAP) };
      });
    });
    const width = PAD * 2 + NET_W + COL_GAP + columns.length * (BOX_W + COL_GAP) - COL_GAP;

    const byId: Record<string, MapDevice> = {};
    devices.forEach((d) => (byId[d.id] = d));
    const edges: Edge[] = [];
    for (const p of Object.values(placed)) {
      const parentId = p.site.parent_id;
      const parent = parentId ? placed[parentId] ?? null : null;
      if (!parent) {
        edges.push({ from: null, to: p, colour: NONE, dashed: false, label: "", title: `${p.site.name}: core` });
        continue;
      }
      // The station at this site whose AP is at the parent site (or the
      // other way round), plus that AP.
      const stations = devices.filter(
        (d) =>
          (d.site?.id === p.site.id && d.ap_site_id === parent.site.id) ||
          (d.site?.id === parent.site.id && d.ap_site_id === p.site.id),
      );
      const radios = [...stations, ...stations.map((s) => (s.ap_device_id ? byId[s.ap_device_id] : undefined)).filter((d): d is MapDevice => !!d)];
      const uses = radios.map((r) => r.downlink_use_pct).filter((v): v is number => v != null);
      edges.push({
        from: parent,
        to: p,
        colour: radios.length ? linkHealth(radios) : NONE,
        dashed: !radios.length,
        label: uses.length ? `${Math.round(Math.max(...uses))}%` : "",
        title: radios.length
          ? `${parent.site.name} → ${p.site.name}: ${[...new Set(radios.map((r) => r.name))].join(" / ")}`
          : `${parent.site.name} → ${p.site.name}: no radio pair found in UISP`,
      });
    }
    return { placed, edges, width, height, loose, hasRoots: roots.length > 0 };
  }, [sites, devices]);

  const siteDevices = useMemo(
    () =>
      selected
        ? devices.filter((d) => d.site?.id === selected).sort((a, b) => Number(a.online) - Number(b.online) || a.name.localeCompare(b.name))
        : [],
    [selected, devices],
  );
  const selectedSite = sites.find((s) => s.id === selected) ?? null;
  const netY = layout.height / 2;

  return (
    <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)]">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-xs text-[var(--text-muted)]">
        <span>Upstream on the left, as on UISP's map. Click a site for its devices.</span>
        {[
          [GOOD, "link fine"],
          [WARN, "busy / low link potential"],
          [BAD, "radio down"],
        ].map(([c, l]) => (
          <span key={l} className="inline-flex items-center gap-1.5">
            <span className="inline-block h-0.5 w-4" style={{ background: c }} />
            {l}
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block w-4 border-t-2 border-dashed" style={{ borderColor: NONE }} />
          no radio pair found
        </span>
        <span>% = downlink use</span>
      </div>
      <div className="overflow-x-auto">
        <svg width={layout.width} height={layout.height} role="img" aria-label="Network topology">
          {layout.hasRoots && (
            <g>
              <circle cx={PAD + 18} cy={netY} r={14} fill="var(--tint-hover)" stroke="var(--border-hairline)" />
              <text x={PAD + 18} y={netY + 30} textAnchor="middle" fontSize={10} fill="var(--text-muted)">Internet</text>
            </g>
          )}
          {layout.edges.map((e) => {
            const x1 = e.from ? e.from.x + BOX_W : PAD + 32;
            const y1 = e.from ? e.from.y + BOX_H / 2 : netY;
            const x2 = e.to.x;
            const y2 = e.to.y + BOX_H / 2;
            const mx = (x1 + x2) / 2;
            const dim = selected && e.to.site.id !== selected && e.from?.site.id !== selected;
            return (
              <g key={`${e.from?.site.id ?? "net"}-${e.to.site.id}`} opacity={dim ? 0.35 : 1}>
                <title>{e.title}</title>
                <path
                  d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`}
                  fill="none"
                  stroke={e.colour}
                  strokeWidth={2}
                  strokeDasharray={e.dashed ? "5 4" : undefined}
                />
                {e.label && (
                  <text x={x2 - 6} y={y2 - 5} textAnchor="end" fontSize={10} fill={e.colour}>
                    {e.label}
                  </text>
                )}
              </g>
            );
          })}
          {Object.values(layout.placed).map((p) => {
            const s = p.site;
            const down = s.devices_offline > 0;
            const active = selected === s.id;
            return (
              <g key={s.id} transform={`translate(${p.x},${p.y})`} style={{ cursor: "pointer" }} onClick={() => setSelected(active ? null : s.id)}>
                <title>{`${s.name} — ${s.device_count} devices${down ? `, ${s.devices_offline} down` : ""}${s.clients != null ? `, ${s.clients} clients` : ""}`}</title>
                <rect
                  width={BOX_W}
                  height={BOX_H}
                  rx={6}
                  fill="var(--surface-1)"
                  stroke={active ? "var(--series-1)" : down ? BAD : "var(--border-hairline)"}
                  strokeWidth={active || down ? 2 : 1}
                />
                <circle cx={14} cy={BOX_H / 2} r={4} fill={down ? BAD : GOOD} />
                <text x={26} y={18} fontSize={12} fontWeight={600} fill="var(--text-primary)">
                  {s.name.length > 17 ? `${s.name.slice(0, 16)}…` : s.name}
                </text>
                <text x={26} y={33} fontSize={10} fill="var(--text-muted)">
                  {s.device_count} devices
                  {down ? <tspan fill={BAD}>{` · ${s.devices_offline} down`}</tspan> : null}
                  {s.clients != null ? ` · ${s.clients} clients` : ""}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      {selectedSite && (
        <div className="border-t border-[var(--border-hairline)] px-3 py-3">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-sm font-semibold">
              {selectedSite.name}
              <span className="ml-2 text-xs font-normal text-[var(--text-muted)]">
                {siteDevices.length} devices in UISP · click a device for its graphs
              </span>
            </p>
            <button type="button" className="text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)]" onClick={() => setSelected(null)}>
              Close
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {siteDevices.map((d) => (
              <button
                key={d.id}
                type="button"
                onClick={() => onPick(d)}
                className="rounded-md border border-[var(--border-hairline)] px-2 py-1 text-left text-xs hover:bg-[var(--tint-hover)]"
              >
                <span className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle" style={{ background: !d.online ? BAD : d.maintenance ? "var(--series-1)" : GOOD }} />
                <span className="font-medium">{d.name}</span>
                <span className="ml-1 text-[var(--text-muted)]">{d.model}</span>
                {d.link_potential_pct != null && <span className="ml-1.5">{Math.round(d.link_potential_pct)}% link</span>}
                {d.downlink_use_pct != null && <span className="ml-1.5">{Math.round(d.downlink_use_pct)}% use</span>}
              </button>
            ))}
          </div>
        </div>
      )}

      {layout.loose.length > 0 && (
        <p className="border-t border-[var(--border-hairline)] px-3 py-2 text-xs text-[var(--text-muted)]">
          Not linked into the network tree in UISP: {layout.loose.map((s) => `${s.name} (${s.device_count})`).join(" · ")}
        </p>
      )}
    </div>
  );
}
