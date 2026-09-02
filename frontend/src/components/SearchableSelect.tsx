import { useEffect, useMemo, useRef, useState } from "react";

/**
 * A type-to-filter replacement for a long <select>.
 *
 * A plain dropdown is fine for six options and unusable for six hundred: to
 * allocate a bank payment you had to scroll a list of every customer looking
 * for one name. Here you type a few letters of the name — or the payment
 * reference, or whatever else `searchText` carries — and the list narrows.
 *
 * Deliberately filtered client-side over a list the parent already holds,
 * rather than querying as you type. Two reasons: the list is needed anyway to
 * show the current selection's label, and a keystroke-per-request picker feels
 * worse than an instant one at these sizes. `hint` is where a caller can say
 * what to do when the list is capped.
 */

export type SearchableOption = {
  value: string;
  label: string;
  /** Everything matching should consider — name, reference, email. Falls back to label. */
  searchText?: string;
  /** Shown dimmed to the right of the label, e.g. a reference or a balance. */
  meta?: string;
};

export function SearchableSelect({
  options,
  value,
  onChange,
  placeholder = "Select…",
  emptyLabel = "Clear selection",
  hint,
  className = "",
  disabled = false,
  required = false,
}: {
  options: SearchableOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  emptyLabel?: string;
  hint?: string;
  className?: string;
  disabled?: boolean;
  /** Keeps the browser's own "please fill this in" behaviour — see the hidden
   *  mirror input in the markup for why that needs help here. */
  required?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  // Which row the arrow keys are on. Kept separate from `value` so moving the
  // highlight never changes the selection until Enter or a click.
  const [activeIndex, setActiveIndex] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const selected = options.find((o) => o.value === value) ?? null;

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    // Every whitespace-separated term must appear somewhere, so "jan eras"
    // finds "Janine Erasmus" and the order you type them doesn't matter.
    const terms = q.split(/\s+/);
    return options.filter((o) => {
      const haystack = `${o.searchText ?? o.label} ${o.meta ?? ""}`.toLowerCase();
      return terms.every((t) => haystack.includes(t));
    });
  }, [options, query]);

  // Close on an outside click or Escape. Without the first one, opening a
  // second picker leaves the first hanging open over the table.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActiveIndex(0);
      // Focus after paint, or the click that opened the list steals it back.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  // Keep the highlighted row in view when arrowing past the visible window.
  useEffect(() => {
    if (!open) return;
    const list = listRef.current;
    const row = list?.children[activeIndex] as HTMLElement | undefined;
    row?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, open]);

  function commit(optionValue: string) {
    onChange(optionValue);
    setOpen(false);
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape") {
      setOpen(false);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex((prev) => {
        const next = prev + step;
        if (next < 0) return Math.max(filtered.length - 1, 0);
        if (next >= filtered.length) return 0;
        return next;
      });
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const option = filtered[activeIndex];
      if (option) commit(option.value);
    }
  }

  const trigger =
    "w-full rounded-md border border-[var(--baseline)] bg-[var(--surface-2)] px-3 py-2 text-left text-sm " +
    "text-[var(--text-primary)] hover:bg-[var(--tint-hover)] disabled:opacity-50";

  return (
    <div ref={containerRef} className={`relative ${className}`}>
      {/* The trigger is a <button>, so `required` on it would mean nothing.
          This mirrors the value into a real form control so an empty
          selection still blocks submit and still gets the browser's own
          message -- the previous <select required> did that for free, and
          losing it would have let a customer-less invoice reach the API and
          come back as a 400.

          Positioned rather than hidden: a display:none or visibility:hidden
          input can't be focused, so the browser refuses to report on it and
          silently blocks the submit with nothing on screen. */}
      {required && (
        <input
          tabIndex={-1}
          aria-hidden="true"
          required
          value={value}
          onChange={() => {}}
          className="absolute bottom-0 left-3 h-0 w-0 opacity-0"
        />
      )}
      <button type="button" className={trigger} disabled={disabled} onClick={() => setOpen((o) => !o)}>
        {selected ? (
          <span>
            {selected.label}
            {selected.meta && <span className="ml-1 text-[var(--text-muted)]">{selected.meta}</span>}
          </span>
        ) : (
          <span className="text-[var(--text-muted)]">{placeholder}</span>
        )}
        <span className="float-right text-[var(--text-muted)]">▾</span>
      </button>

      {open && (
        <div className="absolute z-30 mt-1 w-full min-w-64 rounded-md border border-[var(--baseline)] bg-[var(--surface-1)] shadow-lg">
          <input
            ref={inputRef}
            className="w-full rounded-t-md border-b border-[var(--border-hairline)] bg-[var(--surface-1)] px-3 py-2 text-sm text-[var(--text-primary)] focus:outline-none"
            placeholder="Type to search…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActiveIndex(0);
            }}
            onKeyDown={onKeyDown}
          />
          <ul ref={listRef} className="max-h-56 overflow-y-auto py-1">
            {value && (
              <li>
                <button
                  type="button"
                  className="w-full px-3 py-1.5 text-left text-sm text-[var(--text-muted)] hover:bg-[var(--tint-hover)]"
                  onClick={() => commit("")}
                >
                  {emptyLabel}
                </button>
              </li>
            )}
            {filtered.map((option, index) => (
              <li key={option.value}>
                <button
                  type="button"
                  className={`w-full px-3 py-1.5 text-left text-sm ${
                    index === activeIndex
                      ? "bg-[var(--tint-hover)] text-[var(--text-primary)]"
                      : "text-[var(--text-primary)] hover:bg-[var(--tint-hover)]"
                  }`}
                  // onMouseDown, not onClick: the outside-click handler above
                  // fires on mousedown and would close the list first.
                  onMouseDown={(e) => {
                    e.preventDefault();
                    commit(option.value);
                  }}
                  onMouseEnter={() => setActiveIndex(index)}
                >
                  {option.label}
                  {option.meta && <span className="ml-2 text-xs text-[var(--text-muted)]">{option.meta}</span>}
                </button>
              </li>
            ))}
            {filtered.length === 0 && (
              <li className="px-3 py-2 text-sm text-[var(--text-muted)]">
                Nothing matches “{query.trim()}”.
              </li>
            )}
          </ul>
          {hint && (
            <p className="border-t border-[var(--border-hairline)] px-3 py-1.5 text-xs text-[var(--text-muted)]">
              {hint}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
