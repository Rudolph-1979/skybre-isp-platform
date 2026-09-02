import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { ServiceLiveBandwidth } from "../types";

const POLL_MS = 5000;
// Hard stop, so a forgotten browser tab can't keep a RouterOS session
// churning all afternoon.
const AUTO_STOP_MS = 120_000;

// Instantaneous download/upload for one service, read from its router's
// Simple Queue over the MikroTik API (billing.views.ServiceViewSet
// .live_bandwidth / network.mikrotik.get_service_live_bandwidth).
//
// THIS LOGS INTO THE ROUTER, once every POLL_MS while it is running. That
// is why it no longer starts by itself: it used to poll the moment it
// mounted, so simply opening a service turned into a RouterOS login every
// five seconds for as long as the modal stayed open. Now it is an explicit
// "look at this line right now" diagnostic, and it stops on its own after
// AUTO_STOP_MS.
//
// For ordinary usage figures, use the RADIUS accounting data instead
// (radiusauth.usage) -- the NAS pushes those to us, so they cost nothing
// and scale to every customer at once. The trade is resolution: accounting
// figures move on the router's interim-update interval, typically a minute,
// where this is live to the second.
export function LiveBandwidthWidget({ serviceId }: { serviceId: number }) {
  const [running, setRunning] = useState(false);
  const [data, setData] = useState<ServiceLiveBandwidth | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [stoppedAutomatically, setStoppedAutomatically] = useState(false);
  const startedAt = useRef<number>(0);

  useEffect(() => {
    if (!running) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    startedAt.current = Date.now();
    setLoading(true);

    async function poll() {
      try {
        const res = await api.get<ServiceLiveBandwidth>(`/services/${serviceId}/live-bandwidth/`);
        if (cancelled) return;
        setData(res.data);
        setError(null);
      } catch (err: any) {
        if (cancelled) return;
        setData(null);
        setError(err?.response?.data?.detail || "Couldn't read live bandwidth for this service.");
      } finally {
        if (!cancelled) {
          setLoading(false);
          if (Date.now() - startedAt.current >= AUTO_STOP_MS) {
            setRunning(false);
            setStoppedAutomatically(true);
          } else {
            timer = setTimeout(poll, POLL_MS);
          }
        }
      }
    }
    poll();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [running, serviceId]);

  return (
    <div className="mt-4 rounded border border-[var(--border-hairline)] p-3">
      <div className="mb-1 flex items-center justify-between gap-3">
        <p className="text-xs font-semibold text-[var(--text-primary)]">Live bandwidth (from router)</p>
        <button
          type="button"
          className="text-xs text-[var(--series-1)] hover:underline"
          onClick={() => {
            setStoppedAutomatically(false);
            setRunning((v) => !v);
          }}
        >
          {running ? "Stop" : data ? "Read again" : "Read now"}
        </button>
      </div>

      {loading && <p className="text-xs text-[var(--text-muted)]">Reading…</p>}

      {!loading && data && (
        <p className="text-sm tabular-nums text-[var(--text-primary)]">
          ↓ {data.download_mbps.toFixed(2)} Mbps &nbsp;·&nbsp; ↑ {data.upload_mbps.toFixed(2)} Mbps
        </p>
      )}

      {!loading && !data && error && <p className="text-xs text-[var(--text-muted)]">{error}</p>}

      {!running && !data && !error && (
        <p className="text-xs text-[var(--text-muted)]">
          Queries the router directly. Use for a live look at one line — month-to-date usage comes from
          accounting and needs no router.
        </p>
      )}

      {stoppedAutomatically && (
        <p className="mt-1 text-xs text-[var(--text-muted)]">
          Stopped after 2 minutes to avoid polling the router indefinitely.
        </p>
      )}
    </div>
  );
}
