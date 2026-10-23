import { useEffect, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { StatCard } from "./StatCard";
import { usePolling } from "../hooks/usePolling";

export type UispOutage = {
  id: string;
  type: string;
  in_progress: boolean;
  start: string | null;
  end: string | null;
  duration_s: number;
  device: { id: string; name: string; model: string };
  site: string;
};
export type UispSummary = {
  connected: boolean;
  detail?: string;
  devices: number;
  devices_online: number;
  ptp_radios: number;
  ptp_offline: number;
  aps: number;
  aps_offline: number;
  low_link: number;
  busy: number;
  updates: number;
  maintenance: number;
  outages_open: UispOutage[];
  last_outage: UispOutage | null;
  outages_total: number | null;
  // Low link potential per Network (UISP) tab (15 Oct 2026).
  low_link_tabs?: { ptp: number; ap: number; other: number };
};

/** Dashboard tiles from UISP (14 Oct 2026). Only for staff with Networking
 *  (enabled) and only once UISP is connected -- otherwise no tiles. Returned
 *  as keyed tiles so the dashboard can put them in each person's own order
 *  (15 Oct 2026). */
export function useUispTiles(enabled: boolean): { key: string; node: ReactNode }[] {
  const navigate = useNavigate();
  const [data, setData] = useState<UispSummary | null>(null);
  const load = () => {
    if (!enabled) return;
    api
      .get<UispSummary>("/uisp/summary/")
      .then((r) => setData(r.data))
      .catch(() => setData(null));
  };
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);
  usePolling(load, 120000);

  if (!enabled || !data || !data.connected || data.detail) return [];
  const open = data.outages_open.length;
  const go = (tab: string, extra = "") => () => navigate(`/admin/networking/uisp?tab=${tab}${extra}`);
  // Low link potential opens the tab where most of them are, filtered.
  const lt = data.low_link_tabs ?? { ptp: data.low_link, ap: 0, other: 0 };
  const lowTab = (["ptp", "ap", "other"] as const).reduce((a, b) => (lt[b] > lt[a] ? b : a), "ptp" as "ptp" | "ap" | "other");
  return [
    {
      key: "uisp_outages",
      node: (
        <StatCard
          label="Network outages (UISP)"
          value={open}
          sublabel={
            open
              ? data.outages_open.map((o) => `${o.device.name} (${o.site})`).slice(0, 2).join(", ")
              : data.last_outage
                ? `Last: ${data.last_outage.device.name}`
                : "None open"
          }
          accent={open ? "status-critical" : "status-good"}
          onClick={go("outages")}
          actionLabel="See outages"
        />
      ),
    },
    {
      key: "uisp_backhaul",
      node: (
        <StatCard
          label="Backhaul radios up"
          value={`${data.ptp_radios - data.ptp_offline} / ${data.ptp_radios}`}
          sublabel={`${data.low_link} low link potential · ${data.busy} busy`}
          accent={data.ptp_offline ? "status-critical" : data.low_link || data.busy ? "status-warning" : "status-good"}
          onClick={go("ptp")}
          actionLabel="See backhaul"
        />
      ),
    },
    {
      key: "uisp_aps",
      node: (
        <StatCard
          label="Access points up"
          value={`${data.aps - data.aps_offline} / ${data.aps}`}
          sublabel={data.aps_offline ? `${data.aps_offline} down` : "All answering"}
          accent={data.aps_offline ? "status-critical" : "status-good"}
          onClick={go("ap")}
          actionLabel="See APs"
        />
      ),
    },
    {
      key: "uisp_devices",
      node: (
        <StatCard
          label="UISP devices online"
          value={`${data.devices_online} / ${data.devices}`}
          sublabel={`${data.updates} firmware updates · ${data.maintenance} in maintenance`}
          accent={data.devices_online < data.devices ? "status-warning" : "status-good"}
          onClick={go("other")}
          actionLabel="See network"
        />
      ),
    },
    {
      // Rudolph, 15 Oct 2026: "Also add this tile to the dashboard".
      key: "uisp_low_link",
      node: (
        <StatCard
          label="Low link potential"
          value={data.low_link}
          sublabel="Under 50%"
          accent={data.low_link ? "status-warning" : "status-good"}
          onClick={go(lowTab, "&flag=low_link")}
          actionLabel="See which"
        />
      ),
    },
  ];
}
