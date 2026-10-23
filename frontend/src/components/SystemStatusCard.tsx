import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";

// Dashboard "System status" (Rudolph, 7 Oct 2026), admins only. Server
// figures from GET /api/system-status/ (config/system_status.py), refreshed
// every 5 s while the page is open and visible (Rudolph, 7 Oct 2026: "I need
// a faster refresh rate"; was 30 s). A hidden tab doesn't poll.

type Backup = { at: string; hours: number; ok: boolean; size?: number; file?: string } | null;
type Status = {
  checked_at: string;
  cpu_cores: number;
  load: number[];
  cpu_pct: number;
  iowait_pct: number;
  mem_total: number;
  mem_available: number;
  swap_total: number;
  swap_free: number;
  disk_total: number;
  disk_free: number;
  db_backup: Backup;
  db_backup_configured: boolean;
  remote_backup: Backup;
  remote_backup_configured: boolean;
};

const REFRESH_MS = 5_000;
const OPEN_KEY = "skybre.systemStatus.open";

function gb(bytes: number) {
  return `${(bytes / 1024 ** 3).toFixed(bytes >= 100 * 1024 ** 3 ? 1 : 2)} GB`;
}

function size(bytes: number) {
  return bytes >= 1024 ** 3 ? gb(bytes) : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function ago(hours: number) {
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} min ago`;
  if (hours < 48) return `about ${Math.round(hours)} hour${Math.round(hours) === 1 ? "" : "s"} ago`;
  return `${Math.round(hours / 24)} days ago`;
}

function tone(rawPct: number) {
  // Rounded first, so a bar labelled "90 %" is never still amber.
  const usedPct = Math.round(rawPct);
  return usedPct >= 90 ? "var(--status-critical)" : usedPct >= 75 ? "var(--status-warning)" : "var(--series-3)";
}

/** Used share, coloured by how full: green, amber from 75 %, red from 90 %. */
function Meter({ used, label }: { used: number; label: string }) {
  return (
    <div className="relative h-5 w-44 shrink-0 overflow-hidden rounded bg-[var(--surface-2)] text-[11px] font-semibold">
      <div className="h-full" style={{ width: `${Math.max(1, Math.min(100, used))}%`, background: tone(used) }} />
      <span className="absolute inset-0 flex items-center justify-center text-[var(--text-primary)]">{label}</span>
    </div>
  );
}

function BackupValue({ backup, configured, withSize }: { backup: Backup; configured: boolean; withSize?: boolean }) {
  if (!configured) return <span className="text-xs text-[var(--text-muted)]">Not connected yet</span>;
  if (!backup) return <span className="font-semibold text-[var(--status-critical)]">None found</span>;
  return (
    <span
      className={backup.ok ? "" : "font-semibold text-[var(--status-critical)]"}
      title={`${new Date(backup.at).toLocaleString()}${backup.file ? ` · ${backup.file}` : ""}`}
    >
      {ago(backup.hours)}
      {withSize && backup.size !== undefined ? ` (${size(backup.size)})` : ""}
    </span>
  );
}

export function SystemStatusCard() {
  const [data, setData] = useState<Status | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(() => {
    try {
      return localStorage.getItem(OPEN_KEY) !== "0";
    } catch {
      return true;
    }
  });
  const [, tick] = useState(0);

  useEffect(() => {
    let alive = true;
    const load = () => {
      if (document.visibilityState === "hidden") return;
      api
        .get<Status>("/system-status/")
        .then((res) => {
          if (!alive) return;
          setData(res.data);
          setError("");
        })
        .catch((err) => {
          if (!alive) return;
          const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
          setError(detail || "Couldn't read the server's figures.");
        });
    };
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    const clock = window.setInterval(() => tick((n) => n + 1), 1_000);
    return () => {
      alive = false;
      window.clearInterval(timer);
      window.clearInterval(clock);
    };
  }, []);

  function toggle() {
    setOpen((o) => {
      try {
        localStorage.setItem(OPEN_KEY, o ? "0" : "1");
      } catch {
        /* private window */
      }
      return !o;
    });
  }

  const checked = data ? Math.max(0, Math.round((Date.now() - new Date(data.checked_at).getTime()) / 1000)) : null;
  const rows: [string, ReactNode][] = data
    ? (() => {
        const memUsed = data.mem_total ? 100 * (1 - data.mem_available / data.mem_total) : 0;
        const swapUsed = data.swap_total ? 100 * (1 - data.swap_free / data.swap_total) : 0;
        const diskUsed = data.disk_total ? 100 * (1 - data.disk_free / data.disk_total) : 0;
        const pct = (used: number, total: number) => (total ? ((100 * used) / total).toFixed(1) : "0");
        return [
          ["CPU cores", <span className="font-semibold">{data.cpu_cores}</span>],
          ["Load average (1, 5, 15 min)", <span className="tabular-nums">{data.load.map((v) => v.toFixed(2)).join(", ")}</span>],
          ["CPU usage", <Meter used={data.cpu_pct} label={`${data.cpu_pct.toFixed(1)} %`} />],
          ["I/O wait", <Meter used={data.iowait_pct} label={`${data.iowait_pct.toFixed(1)} %`} />],
          [
            `Memory: ${gb(data.mem_total)} (${pct(data.mem_available, data.mem_total)}% free)`,
            <Meter used={memUsed} label={`${memUsed.toFixed(0)} % used`} />,
          ],
          [
            data.swap_total ? `Swap: ${gb(data.swap_total)} (${pct(data.swap_free, data.swap_total)}% free)` : "Swap",
            data.swap_total ? <Meter used={swapUsed} label={`${swapUsed.toFixed(0)} % used`} /> : <span className="text-xs text-[var(--text-muted)]">None</span>,
          ],
          [
            `Disk: ${gb(data.disk_total)} (${pct(data.disk_free, data.disk_total)}% free)`,
            <Meter used={diskUsed} label={`${diskUsed.toFixed(0)} % used`} />,
          ],
          ["Last database backup", <BackupValue backup={data.db_backup} configured={data.db_backup_configured} withSize />],
          ["Last remote backup", <BackupValue backup={data.remote_backup} configured={data.remote_backup_configured} />],
        ];
      })()
    : [];

  return (
    <section className="mt-6 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)]">
      <button type="button" onClick={toggle} className="flex w-full items-center justify-between px-5 py-4 text-left">
        <span className="text-sm font-semibold text-[var(--text-primary)]">System status</span>
        <span className="text-xs text-[var(--text-muted)]">
          {checked !== null ? `Updated ${checked < 60 ? `${checked} s` : `${Math.round(checked / 60)} min`} ago · ` : ""}
          {open ? "▲" : "▼"}
        </span>
      </button>
      {open && (
        <div className="border-t border-[var(--border-hairline)] text-sm">
          {error && <p className="px-5 py-3 text-[var(--status-critical)]">{error}</p>}
          {!data && !error && <p className="px-5 py-3 text-[var(--text-muted)]">Reading the server…</p>}
          <ul className="divide-y divide-[var(--border-hairline)]">
            {rows.map(([label, value]) => (
              <li key={label} className="flex items-center justify-between gap-4 px-5 py-2">
                <span className="text-[var(--text-secondary)]">{label}</span>
                {value}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
