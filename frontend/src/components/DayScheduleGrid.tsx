import { useEffect, useMemo, useRef, useState } from "react";
import type { Job, Shift } from "../types";

// One day laid out against the hours -- the body of the Scheduling page's
// Day view. The month grid can only show three items per cell before it
// gives up and says "+2 more"; this is where you actually read a day.
//
// Blocks are positioned and sized by their real start and end rather than
// bucketed into the hour they begin in -- a 3-hour installation should look
// like three hours. Overlapping items sit side by side, because two jobs at
// once is the situation you most need to see.

const HOUR_HEIGHT = 52;
// How often the "now" line moves. A minute is the smallest visible step at
// this scale -- 52px an hour means under a pixel per minute, so ticking
// faster would repaint for nothing.
const NOW_TICK_MS = 60_000;
const START_HOUR = 0;
const END_HOUR = 24;
// Blocks shorter than this would render as an unreadable sliver.
const MIN_BLOCK_HEIGHT = 20;

type Item = {
  key: string;
  kind: "job" | "shift";
  label: string;
  sublabel: string;
  colour: string;
  start: Date;
  end: Date;
  cancelled: boolean;
  onClick: () => void;
};

function minutesFromMidnight(d: Date) {
  return d.getHours() * 60 + d.getMinutes();
}

function timeLabel(d: Date) {
  return d.toLocaleTimeString("en-ZA", { hour: "2-digit", minute: "2-digit" });
}

/** Assign overlapping items to side-by-side columns.
 *
 * Greedy sweep: items are laid out in start order, and each takes the first
 * column whose previous occupant has already finished. `columns` is the
 * width divisor for that item's own overlap group, so two overlapping jobs
 * each take half the width while a lone job takes all of it. */
type PlacedItem = Item & { column: number; columns: number };

function assignColumns(items: Item[]): PlacedItem[] {
  const sorted = [...items].sort((a, b) => a.start.getTime() - b.start.getTime());
  const placed: PlacedItem[] = [];
  let group: PlacedItem[] = [];
  let groupEnd = 0;

  const flush = () => {
    const width = group.reduce((m, g) => Math.max(m, g.column + 1), 1);
    group.forEach((g) => (g.columns = width));
    placed.push(...group);
    group = [];
    groupEnd = 0;
  };

  for (const item of sorted) {
    if (group.length && item.start.getTime() >= groupEnd) {
      // No overlap with anything in the current group -- close it off, so
      // one long job early in the day doesn't squeeze the whole day narrow.
      flush();
    }
    const columnEnds = new Map<number, number>();
    group.forEach((g) => {
      columnEnds.set(g.column, Math.max(columnEnds.get(g.column) ?? 0, g.end.getTime()));
    });
    let column = 0;
    while ((columnEnds.get(column) ?? 0) > item.start.getTime()) column += 1;
    group.push({ ...item, column, columns: 1 });
    groupEnd = Math.max(groupEnd, item.end.getTime());
  }
  flush();
  return placed;
}

export function DayScheduleGrid({
  day,
  jobs,
  shifts,
  jobColour,
  shiftColour,
  onNewJob,
  onNewShift,
  onEditJob,
  onEditShift,
}: {
  day: Date;
  jobs: Job[];
  shifts: Shift[];
  jobColour: (job: Job) => string;
  shiftColour: string;
  onNewJob: (at: Date) => void;
  onNewShift: (at: Date) => void;
  onEditJob: (job: Job) => void;
  onEditShift: (shift: Shift) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Current time, re-read every minute so the marker travels down the day.
  // Kept in state rather than computed once at render, or it would freeze
  // wherever it was when the page loaded.
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), NOW_TICK_MS);
    return () => clearInterval(timer);
  }, []);

  // Only meaningful on today. Showing it on any other date would claim
  // "now" is somewhere in a day that isn't happening.
  const isToday =
    now.getFullYear() === day.getFullYear() &&
    now.getMonth() === day.getMonth() &&
    now.getDate() === day.getDate();

  const items = useMemo<PlacedItem[]>(() => {
    const jobItems: Item[] = jobs.map((j) => ({
      key: `job-${j.id}`,
      kind: "job",
      label: j.title,
      sublabel: j.customer_name ?? j.assigned_to_name ?? "standalone",
      colour: jobColour(j),
      start: new Date(j.start),
      end: new Date(j.end),
      cancelled: j.status === "cancelled",
      onClick: () => onEditJob(j),
    }));
    const shiftItems: Item[] = shifts.map((s) => ({
      key: `shift-${s.id}`,
      kind: "shift",
      label: `${s.staff_name} — shift`,
      sublabel: s.role_note || "",
      colour: shiftColour,
      start: new Date(s.start),
      end: new Date(s.end),
      cancelled: s.status === "cancelled",
      onClick: () => onEditShift(s),
    }));
    return assignColumns([...jobItems, ...shiftItems]);
  }, [jobs, shifts, jobColour, shiftColour, onEditJob, onEditShift]);

  // Open on the working day rather than at midnight. Scrolls to the
  // earliest item if there is one, so a 05:00 callout isn't hidden above
  // the fold.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const earliest = items.reduce<number | null>(
      (min, i) => (min === null ? minutesFromMidnight(i.start) : Math.min(min, minutesFromMidnight(i.start))),
      null
    );
    // On today, open where the day actually is. Otherwise the earliest
    // item, falling back to the start of a normal working day.
    const target = isToday
      ? Math.max(0, minutesFromMidnight(new Date()) - 90)
      : earliest === null
      ? 7 * 60
      : Math.max(0, earliest - 60);
    el.scrollTop = (target / 60) * HOUR_HEIGHT;
    // Deliberately not depending on `now`: this positions the view when the
    // day changes, and must not yank the scroll back every minute while
    // someone is looking at another part of the day.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, day]);

  const hours = Array.from({ length: END_HOUR - START_HOUR }, (_, i) => START_HOUR + i);

  function newAt(hour: number, kind: "job" | "shift") {
    const at = new Date(day);
    at.setHours(hour, 0, 0, 0);
    (kind === "job" ? onNewJob : onNewShift)(at);
  }

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-[var(--text-muted)]">
          {items.length === 0
            ? "Nothing scheduled — click an hour to add a job."
            : `${jobs.length} job${jobs.length === 1 ? "" : "s"}, ${shifts.length} shift${
                shifts.length === 1 ? "" : "s"
              } — click an hour to add, or an item to edit.`}
        </p>
        <button
          type="button"
          className="text-xs text-[var(--series-1)] hover:underline"
          onClick={() => newAt(8, "shift")}
        >
          + Add a shift on this day
        </button>
      </div>

      <div
        ref={scrollRef}
        className="relative max-h-[70vh] overflow-y-auto rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] shadow-sm"
      >
        <div className="relative" style={{ height: (END_HOUR - START_HOUR) * HOUR_HEIGHT }}>
          {hours.map((hour) => (
            <div
              key={hour}
              className="absolute left-0 right-0 border-t border-[var(--border-hairline)]"
              style={{ top: (hour - START_HOUR) * HOUR_HEIGHT, height: HOUR_HEIGHT }}
            >
              <span className="absolute -top-2 left-1 bg-[var(--surface-1)] px-1 text-[10px] tabular-nums text-[var(--text-muted)]">
                {String(hour).padStart(2, "0")}:00
              </span>
              {/* The empty strip is the click target for "add here". Sits
                  under the blocks so it never steals a click from an item. */}
              <button
                type="button"
                title={`Add a job at ${String(hour).padStart(2, "0")}:00`}
                onClick={() => newAt(hour, "job")}
                className="absolute inset-0 ml-14 w-auto cursor-pointer hover:bg-[var(--tint-hover)]"
                style={{ right: 0 }}
              />
            </div>
          ))}

          {isToday && (
            <div
              className="pointer-events-none absolute left-0 right-0 z-10"
              style={{ top: ((minutesFromMidnight(now) - START_HOUR * 60) / 60) * HOUR_HEIGHT }}
            >
              <div className="relative h-0 border-t-2 border-[#e5484d]">
                <span className="absolute -left-0.5 -top-[5px] h-2 w-2 rounded-full bg-[#e5484d]" />
                <span className="absolute right-1 -top-[9px] rounded bg-[#e5484d] px-1 text-[10px] font-medium tabular-nums text-white">
                  {timeLabel(now)}
                </span>
              </div>
            </div>
          )}

          {items.map((item) => {
            const startMin = minutesFromMidnight(item.start);
            // An item ending after midnight would otherwise render with a
            // negative height -- clamp it to the end of the day instead.
            const endsNextDay = item.end.getDate() !== item.start.getDate();
            const endMin = endsNextDay ? END_HOUR * 60 : minutesFromMidnight(item.end);
            const top = ((startMin - START_HOUR * 60) / 60) * HOUR_HEIGHT;
            const height = Math.max(MIN_BLOCK_HEIGHT, ((endMin - startMin) / 60) * HOUR_HEIGHT);
            const widthPct = 100 / item.columns;

            return (
              <button
                key={item.key}
                type="button"
                onClick={item.onClick}
                title={`${timeLabel(item.start)}–${timeLabel(item.end)} ${item.label}`}
                className="absolute overflow-hidden rounded px-1.5 py-0.5 text-left text-[11px] font-medium text-white shadow-sm"
                style={{
                  top,
                  height,
                  left: `calc(3.5rem + ${item.column * widthPct}%)`,
                  width: `calc(${widthPct}% - 3.5rem / ${item.columns} - 4px)`,
                  background: item.colour,
                  opacity: item.cancelled ? 0.45 : 1,
                  textDecoration: item.cancelled ? "line-through" : undefined,
                }}
              >
                <span className="block truncate">
                  {timeLabel(item.start)} {item.label}
                </span>
                {height > 32 && item.sublabel && (
                  <span className="block truncate opacity-80">{item.sublabel}</span>
                )}
              </button>
            );
          })}
        </div>
      </div>

    </div>
  );
}
