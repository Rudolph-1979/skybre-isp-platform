import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { StatCard } from "./StatCard";
import { Modal } from "./Modal";
import { usePolling } from "../hooks/usePolling";

/**
 * Dashboard tile "Speeding today" (#188, Rudolph 16 Oct 2026: which vehicle
 * sped and how fast). From Webfleet's speeding events, stored by the
 * 15-minute sync. Hidden without Webfleet or without the Vehicles section.
 */

type SpeedingEvent = {
  id: number;
  vehicle_id: number;
  registration: string;
  vehicle_name: string;
  started_at: string;
  max_speed_kmh: number | null;
  avg_speed_kmh: number | null;
  limit_kmh: number | null;
  place: string;
  driver_name: string;
  duration_s: number;
};

function time(iso: string) {
  return new Date(iso).toLocaleTimeString("en-ZA", { hour: "2-digit", minute: "2-digit" });
}
function speedText(e: SpeedingEvent) {
  return `${e.max_speed_kmh ?? "?"} km/h${e.limit_kmh ? ` in a ${e.limit_kmh}` : ""}`;
}

export function useSpeedingTile(enabled: boolean): { tiles: { key: string; node: ReactNode }[]; modal: ReactNode } {
  const [data, setData] = useState<{ configured: boolean; count: number; results: SpeedingEvent[] } | null>(null);
  const [open, setOpen] = useState(false);
  const load = () => {
    if (!enabled) return;
    api
      .get("/webfleet/speeding/")
      .then((r) => setData(r.data))
      .catch(() => setData(null));
  };
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);
  usePolling(load, 120000);

  if (!enabled || !data || !data.configured) return { tiles: [], modal: null };

  // Worst first: how far over the limit, then how fast.
  const events = [...data.results].sort(
    (a, b) =>
      (b.max_speed_kmh ?? 0) - (b.limit_kmh ?? 0) - ((a.max_speed_kmh ?? 0) - (a.limit_kmh ?? 0)) ||
      (b.max_speed_kmh ?? 0) - (a.max_speed_kmh ?? 0),
  );
  const sub = events.length
    ? events
        .slice(0, 2)
        .map((e) => `${e.registration} ${speedText(e)}`)
        .join(" · ") + (events.length > 2 ? ` · +${events.length - 2} more` : "")
    : "No speeding reported today";

  return {
    tiles: [
      {
        key: "speeding_today",
        node: (
          <StatCard
            label="Speeding today (Webfleet)"
            value={data.count}
            sublabel={sub}
            accent={data.count ? "status-critical" : "status-good"}
            onClick={() => setOpen(true)}
            actionLabel="See which"
          />
        ),
      },
    ],
    modal: open ? (
      <Modal title="Speeding today" onClose={() => setOpen(false)}>
        {events.length === 0 ? (
          <p className="text-sm text-[var(--text-secondary)]">Webfleet hasn&rsquo;t reported any speeding today.</p>
        ) : (
          <ul className="divide-y divide-[var(--border-hairline)]">
            {events.map((e) => (
              <li key={e.id} className="flex items-start justify-between gap-3 py-2.5 text-sm">
                <div className="min-w-0">
                  <Link
                    to={`/admin/vehicles/${e.vehicle_id}`}
                    className="font-medium text-[var(--text-primary)] hover:underline"
                    onClick={() => setOpen(false)}
                  >
                    {e.registration}
                  </Link>
                  <span className="ml-1 text-xs text-[var(--text-muted)]">{e.vehicle_name}</span>
                  <p className="text-xs text-[var(--text-muted)]">
                    {time(e.started_at)}
                    {e.driver_name ? ` · ${e.driver_name}` : ""}
                    {e.place ? ` · ${e.place}` : ""}
                  </p>
                </div>
                <div className="shrink-0 text-right">
                  <p className="font-semibold text-[var(--status-critical)] tabular-nums">{e.max_speed_kmh ?? "?"} km/h</p>
                  <p className="text-xs text-[var(--text-muted)]">
                    {e.limit_kmh ? `limit ${e.limit_kmh}` : "limit unknown"}
                    {e.duration_s ? ` · ${Math.max(1, Math.round(e.duration_s / 60))} min` : ""}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-xs text-[var(--text-muted)]">From Webfleet, updated every 15 minutes.</p>
      </Modal>
    ) : null,
  };
}
