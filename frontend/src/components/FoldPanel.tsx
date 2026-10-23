import { useState, type ReactNode } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";

/**
 * Foldable full-width panels (#193, Rudolph 16 Oct 2026: "Make the panels
 * foldable in order to read better all the info"). Which panels are open is
 * remembered per person on their account (User.panel_state[page] = the open
 * panel keys), like the dashboard tile order. A folded panel shows a
 * one-line summary; its action button stays usable in the header.
 */

// What this browser session has saved, so moving between vehicles shows
// the latest choice without re-reading /me/.
const saved: Record<string, string[]> = {};

export function usePanelState(page: string, defaultOpen: string[]) {
  const { user } = useAuth();
  const [open, setOpen] = useState<string[]>(
    () => saved[page] ?? user?.panel_state?.[page] ?? defaultOpen,
  );

  function store(next: string[]) {
    setOpen(next);
    saved[page] = next;
    api.patch("/me/", { panel_state: { [page]: next } }).catch(() => {
      /* still works for this visit; just not remembered */
    });
  }

  return {
    isOpen: (key: string) => open.includes(key),
    toggle: (key: string) => store(open.includes(key) ? open.filter((k) => k !== key) : [...open, key]),
    setAll: (keys: string[]) => store(keys),
  };
}

export function FoldPanel({
  title,
  count,
  summary,
  actions,
  open,
  onToggle,
  children,
}: {
  title: string;
  count?: number | null;
  summary?: ReactNode;
  actions?: ReactNode;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <section className="mb-3 overflow-hidden rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] shadow-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2.5 hover:bg-[var(--tint-hover)]">
        <button
          type="button"
          aria-expanded={open}
          onClick={onToggle}
          className="flex min-w-0 flex-1 items-center gap-3 text-left"
        >
          <svg
            viewBox="0 0 20 20"
            fill="currentColor"
            aria-hidden="true"
            className={`h-4 w-4 flex-none text-[var(--text-muted)] transition-transform ${open ? "rotate-90" : ""}`}
          >
            <path d="M7 4l6 6-6 6z" />
          </svg>
          <span className="whitespace-nowrap text-sm font-semibold text-[var(--text-primary)]">{title}</span>
          {count != null && (
            <span className="flex-none rounded-full border border-[var(--border-hairline)] px-2 text-xs tabular-nums text-[var(--text-muted)]">
              {count.toLocaleString()}
            </span>
          )}
          {summary && (
            <span
              className={`hidden min-w-0 flex-1 truncate text-xs text-[var(--text-secondary)] sm:block ${open ? "invisible" : ""}`}
            >
              {summary}
            </span>
          )}
        </button>
        {actions && <div className="flex flex-none items-center gap-2">{actions}</div>}
      </div>
      {open && <div className="border-t border-[var(--border-hairline)]">{children}</div>}
    </section>
  );
}

/** A table inside a panel: no second border/shadow of its own. */
export function PanelTable({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">{children}</table>
    </div>
  );
}

/** First `limit` rows, then a "Show all" link under the table. */
export function useShowMore<T>(rows: T[], limit = 15) {
  const [all, setAll] = useState(false);
  const visible = all ? rows : rows.slice(0, limit);
  const footer =
    rows.length > limit ? (
      <div className="border-t border-[var(--border-hairline)] px-4 py-2 text-xs text-[var(--text-muted)]">
        Showing {visible.length} of {rows.length} ·{" "}
        <button type="button" className="text-[var(--series-1)] hover:underline" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : "Show all"}
        </button>
      </div>
    ) : null;
  return { visible, footer };
}
