import { useState, type ReactNode } from "react";
import { btnPrimary, btnSecondary } from "./Modal";

/**
 * Dashboard tiles in each person's own order (Rudolph, 15 Oct 2026: "Can we
 * make the tile movable and remembered for each users profile?").
 *
 * "Arrange tiles" on the dashboard turns on arrange mode: drag a tile onto
 * another to move it (or use the arrows, which also work on a phone), then
 * Save. The order is stored on the person's account (User.dashboard_layout),
 * so it follows them to any browser. Tiles they can't see are skipped; a tile
 * added later slots in next to its built-in neighbour.
 */

export type DashboardTile = { key: string; node: ReactNode };

/** `base` in its own order, plus every key of `reference` missing from it,
 *  each put straight after its nearest earlier neighbour in `reference`. */
export function fillIn(base: string[], reference: string[]): string[] {
  const out = [...base];
  reference.forEach((k, i) => {
    if (out.includes(k)) return;
    let pos = 0;
    for (let j = i - 1; j >= 0; j--) {
      const at = out.indexOf(reference[j]);
      if (at >= 0) {
        pos = at + 1;
        break;
      }
    }
    out.splice(pos, 0, k);
  });
  return out;
}

/** The order to show: the saved order for the tiles present, new tiles beside their default neighbour. */
export function orderedKeys(defaultKeys: string[], saved: string[]): string[] {
  if (!saved.length) return defaultKeys;
  const present = new Set(defaultKeys);
  return fillIn(
    saved.filter((k) => present.has(k)),
    defaultKeys,
  );
}

export function DashboardTileGrid({
  tiles,
  saved,
  arranging,
  onSave,
  onCancel,
}: {
  tiles: DashboardTile[];
  saved: string[];
  arranging: boolean;
  onSave: (order: string[]) => Promise<void>;
  onCancel: () => void;
}) {
  const defaultKeys = tiles.map((t) => t.key);
  const byKey = new Map(tiles.map((t) => [t.key, t.node]));
  const [draft, setDraft] = useState<string[] | null>(null);
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const current = orderedKeys(defaultKeys, saved);
  // The draft starts from what is on screen the moment arrange mode opens;
  // a tile that loads while arranging joins it beside its default neighbour.
  const keys = arranging ? orderedKeys(defaultKeys, draft ?? current) : current;

  function move(key: string, toIndex: number) {
    const next = keys.filter((k) => k !== key);
    next.splice(Math.max(0, Math.min(toIndex, next.length)), 0, key);
    setDraft(next);
  }

  function finish() {
    setDraft(null);
    setDragKey(null);
    setError("");
  }

  async function save(order: string[]) {
    setBusy(true);
    setError("");
    try {
      // Tiles not on screen right now (UISP not answering, a section's
      // data not loaded) keep their place in the saved order.
      await onSave(order.length ? fillIn(order, saved) : []);
      finish();
    } catch {
      setError("Couldn't save the order. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {arranging && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-dashed border-[var(--series-1)] bg-[var(--surface-1)] px-3 py-2">
          <p className="text-sm text-[var(--text-secondary)]">
            Drag a tile onto another to move it, or use the arrows. Saved to your profile.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            {error && <span className="text-xs text-[var(--status-critical)]">{error}</span>}
            <button type="button" className={btnSecondary} disabled={busy} onClick={() => save([])}>
              Reset to default
            </button>
            <button
              type="button"
              className={btnSecondary}
              disabled={busy}
              onClick={() => {
                finish();
                onCancel();
              }}
            >
              Cancel
            </button>
            <button type="button" className={btnPrimary} disabled={busy} onClick={() => save(keys)}>
              {busy ? "Saving…" : "Save order"}
            </button>
          </div>
        </div>
      )}
      <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
        {keys.map((key, i) => (
          <div
            key={key}
            data-tile={key}
            className={`relative [&>*]:h-full ${dragKey === key ? "opacity-40" : ""}`}
            draggable={arranging}
            onDragStart={(e) => {
              if (!arranging) return;
              setDragKey(key);
              e.dataTransfer.effectAllowed = "move";
              e.dataTransfer.setData("text/plain", key);
            }}
            onDragOver={(e) => {
              if (!arranging || !dragKey) return;
              e.preventDefault();
              if (dragKey !== key) move(dragKey, i);
            }}
            onDrop={(e) => {
              e.preventDefault();
              setDragKey(null);
            }}
            onDragEnd={() => setDragKey(null)}
          >
            {byKey.get(key)}
            {arranging && (
              // Covers the tile so a click doesn't open its list while arranging.
              <div className="absolute inset-0 cursor-grab rounded-lg border-2 border-dashed border-[var(--series-1)] active:cursor-grabbing">
                <span className="absolute right-1.5 top-1.5 flex gap-1">
                  <button
                    type="button"
                    aria-label="Move earlier"
                    disabled={i === 0}
                    className="rounded border border-[var(--border-hairline)] bg-[var(--surface-2)] px-1.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-30"
                    onClick={() => move(key, i - 1)}
                  >
                    ◀
                  </button>
                  <button
                    type="button"
                    aria-label="Move later"
                    disabled={i === keys.length - 1}
                    className="rounded border border-[var(--border-hairline)] bg-[var(--surface-2)] px-1.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-30"
                    onClick={() => move(key, i + 1)}
                  >
                    ▶
                  </button>
                </span>
              </div>
            )}
          </div>
        ))}
      </div>
    </>
  );
}
