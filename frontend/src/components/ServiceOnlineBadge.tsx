import { formatIsoDateTime } from "../utils/billingTerms";

/** Online / Offline pill for one service line, next to its status
 *  (Rudolph, 8 Oct 2026). Nothing for a line with no RADIUS login here or
 *  one that isn't Active -- see radiusauth.offline.service_connection_state. */
export function ServiceOnlineBadge({ online, lastSeen }: { online?: boolean | null; lastSeen?: string | null }) {
  if (online == null) return null;
  const pill =
    "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-[var(--tint-hover)] px-2 py-0.5 text-xs text-[var(--text-primary)]";
  if (online) {
    return (
      <span className={pill} title={lastSeen ? `Last heard from ${formatIsoDateTime(lastSeen)}` : undefined}>
        <span className="h-1.5 w-1.5 rounded-full bg-[var(--status-ok,#22c55e)]" aria-hidden="true" />
        Online
      </span>
    );
  }
  return (
    <span
      className={pill}
      title={lastSeen ? `Last seen ${formatIsoDateTime(lastSeen)}` : "No session has ever been recorded for this line."}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-[var(--status-critical)]" aria-hidden="true" />
      Offline
    </span>
  );
}

/** "Last seen …" under an offline line's status. */
export function ServiceLastSeen({ online, lastSeen }: { online?: boolean | null; lastSeen?: string | null }) {
  if (online !== false) return null;
  return (
    <span className="mt-0.5 block whitespace-nowrap text-xs text-[var(--text-muted)]">
      {lastSeen ? `Last seen ${formatIsoDateTime(lastSeen)}` : "Never connected"}
    </span>
  );
}
