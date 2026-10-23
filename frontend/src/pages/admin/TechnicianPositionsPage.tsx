import { useMemo } from "react";
import { MapContainer, TileLayer, Marker, Polyline, Popup } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

import { useApiList } from "../../hooks/useApiList";
import { usePolling } from "../../hooks/usePolling";
import { PageHeader } from "../../components/PageHeader";
import type { TechnicianPosition } from "../../types";

// Kroonstad, roughly. Only used when there is nothing to show -- the map
// fits itself to the pins whenever there are any.
const FALLBACK_CENTRE: [number, number] = [-27.6547, 27.2349];

// Faster than the calendar's 30 seconds, because this is the screen
// somebody watches while a technician is on the way. Conecto pings once a
// minute, so anything under that is asking the server for news that
// cannot have arrived yet.
const REFRESH_MS = 20_000;

export const STATUS_COLOR: Record<string, string> = {
  en_route: "#eda100",
  checked_in: "#1baf7a",
  completed: "#898781",
  cancelled: "#e34948",
};

const STATUS_LABEL: Record<string, string> = {
  pending: "Pending",
  scheduled: "Pending",
  en_route: "En route",
  checked_in: "Checked in",
  checked_out: "Completed",
  completed: "Completed",
  cancelled: "Cancelled",
};

export function statusLabel(value: string) {
  return STATUS_LABEL[value] ?? value.replace(/_/g, " ");
}

/** How old the reading is, in words, because the number is the whole point. */
function agoLabel(iso: string | null) {
  if (!iso) return "never reported";
  const minutes = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

/**
 * What this pin is actually claiming, in one phrase.
 *
 * The distinction the whole screen turns on. A live ping and a check-in
 * from three hours ago draw an identical dot, and the person reading it
 * is deciding whether to send somebody else to a callout.
 */
export function claimLabel(p: TechnicianPosition) {
  if (p.lat === null) return "no position reported";
  if (p.live) return `live · ${agoLabel(p.occurred_at)}`;
  if (p.source === "ping") return `last ping ${agoLabel(p.occurred_at)}`;
  return `last seen ${agoLabel(p.occurred_at)}`;
}

/**
 * A coloured dot with the technician's name beside it.
 *
 * divIcon rather than Leaflet's default marker: the stock icons are PNGs
 * resolved relative to the CSS, which bundlers rewrite and then fail to
 * find -- the classic "markers are invisible in production" problem. This
 * has no image assets at all, and carries the status colour for free.
 *
 * A live pin gets a halo and full opacity; everything else is faded. The
 * fade is doing real work, not decoration: it is the difference between a
 * position and a memory of one, visible from across the room.
 */
export function pin(p: TechnicianPosition) {
  const color = STATUS_COLOR[p.status] ?? "#4a3aa7";
  const halo = p.live
    ? `box-shadow:0 0 0 1px rgba(0,0,0,.35),0 0 0 6px ${color}33;`
    : "box-shadow:0 0 0 1px rgba(0,0,0,.35);";
  return L.divIcon({
    className: "",
    iconSize: [12, 12],
    iconAnchor: [6, 6],
    html: `
      <div style="display:flex;align-items:center;gap:6px;white-space:nowrap">
        <span style="width:12px;height:12px;border-radius:50%;background:${color};
                     border:2px solid #fff;${halo}
                     opacity:${p.live ? 1 : 0.5}"></span>
        <span style="font:500 11px/1.2 system-ui,sans-serif;color:#1c1c1c;
                     background:rgba(255,255,255,.85);padding:1px 4px;border-radius:3px;
                     opacity:${p.live ? 1 : 0.65}">${p.name}</span>
      </div>`,
  });
}

export function TechnicianPositionsPage() {
  const { items, loading, refetchQuietly } = useApiList<TechnicianPosition>(
    "/technician-positions/"
  );
  usePolling(refetchQuietly, REFRESH_MS);

  const placed = useMemo(
    () => items.filter((p) => p.lat !== null && p.lng !== null),
    [items]
  );
  const liveCount = useMemo(() => items.filter((p) => p.live).length, [items]);

  const bounds = useMemo(() => {
    if (!placed.length) return null;
    return L.latLngBounds(placed.map((p) => [p.lat as number, p.lng as number]));
  }, [placed]);

  return (
    <div>
      <PageHeader
        title="Technician positions"
        subtitle={
          liveCount === 1
            ? "1 technician reporting live"
            : `${liveCount} technicians reporting live`
        }
      />

      {/* Said plainly and permanently, not in a tooltip. The two kinds of
          pin look identical, and somebody deciding whether to send a
          second technician to a callout has to know which one they are
          looking at. */}
      <p className="mb-4 rounded-md border border-[var(--border-hairline)] bg-[var(--surface-1)] px-3 py-2 text-xs text-[var(--text-secondary)]">
        The Conecto app reports a position every minute while a technician is <strong>en route</strong> —
        between tapping Start and checking in. Those pins are live and trail the route taken. Outside
        that window nothing is reported, so a faded pin is the last place a status change came from,
        which is usually the customer&rsquo;s own address and may be hours old. Every row says which it is.
      </p>

      {loading && !placed.length ? (
        <p className="text-[var(--text-muted)]">Loading…</p>
      ) : (
        <>
          <div className="overflow-hidden rounded-lg border border-[var(--border-hairline)]">
            <MapContainer
              center={FALLBACK_CENTRE}
              zoom={11}
              bounds={bounds ?? undefined}
              boundsOptions={{ padding: [40, 40], maxZoom: 14 }}
              style={{ height: "60vh", width: "100%" }}
              scrollWheelZoom
            >
              <TileLayer
                attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              />

              {/* The drive, behind the pin. Two points is a straight line
                  between two places rather than a route, so it is not
                  worth drawing -- the threshold keeps a single stale ping
                  from looking like a journey. */}
              {items
                .filter((p) => p.trail.length > 2)
                .map((p) => (
                  <Polyline
                    key={`trail-${p.code}`}
                    positions={p.trail}
                    pathOptions={{
                      color: STATUS_COLOR[p.status] ?? "#4a3aa7",
                      weight: 3,
                      opacity: p.live ? 0.7 : 0.3,
                    }}
                  />
                ))}

              {placed.map((p) => (
                <Marker
                  key={p.code}
                  position={[p.lat as number, p.lng as number]}
                  icon={pin(p)}
                >
                  <Popup>
                    <div className="text-xs">
                      <div className="font-semibold">{p.name}</div>
                      <div>
                        {statusLabel(p.status)} · {claimLabel(p)}
                      </div>
                      {p.job_title && (
                        <div className="mt-1">
                          {p.job_title}
                          {p.customer_name ? ` — ${p.customer_name}` : ""}
                        </div>
                      )}
                    </div>
                  </Popup>
                </Marker>
              ))}
            </MapContainer>
          </div>

          {/* The list is not decoration. A technician whose phone has sent
              nothing has no pin at all, and a map alone would simply not
              mention them -- which reads as "nothing to report" rather
              than "we do not know where this person is". */}
          <div className="mt-4 space-y-1 text-sm">
            {items.map((p) => (
              <div
                key={p.code}
                className="flex flex-wrap items-baseline gap-x-2 rounded-md border border-[var(--border-hairline)] bg-[var(--surface-1)] px-3 py-2"
              >
                {p.live && (
                  <span
                    className="inline-block h-2 w-2 rounded-full"
                    style={{ background: STATUS_COLOR[p.status] ?? "#4a3aa7" }}
                    aria-hidden
                  />
                )}
                <span className="font-medium">{p.name}</span>
                <span className="text-[var(--text-secondary)]">{statusLabel(p.status)}</span>
                <span
                  className={
                    p.live
                      ? "font-medium text-[var(--text-secondary)]"
                      : "text-[var(--text-muted)]"
                  }
                >
                  {claimLabel(p)}
                </span>
                {p.job_title && (
                  <span className="ml-auto text-[var(--text-muted)]">
                    {p.job_title}
                    {p.customer_name ? ` — ${p.customer_name}` : ""}
                  </span>
                )}
              </div>
            ))}
            {items.length === 0 && (
              <p className="text-[var(--text-muted)]">
                No technician has reported a position yet today.
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
