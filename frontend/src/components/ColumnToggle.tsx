import { useEffect, useRef, useState } from "react";
import { btnSecondary } from "./Modal";

export interface ColumnDef {
  key: string;
  label: string;
}

export function ColumnToggle({
  columns,
  hidden,
  onToggle,
  alwaysVisible = [],
}: {
  columns: ColumnDef[];
  hidden: Set<string>;
  onToggle: (key: string) => void;
  alwaysVisible?: string[];
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  return (
    <div className="relative" ref={ref}>
      <button type="button" className={btnSecondary} onClick={() => setOpen((o) => !o)}>
        Columns
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-1 w-56 rounded-md border border-[var(--border-hairline)] bg-[var(--surface-1)] p-2 shadow-lg">
          {columns.map((c) => {
            const locked = alwaysVisible.includes(c.key);
            return (
              <label
                key={c.key}
                className={`flex items-center gap-2 rounded px-2 py-1.5 text-sm ${
                  locked ? "text-[var(--text-muted)]" : "cursor-pointer hover:bg-[var(--tint-hover)]"
                }`}
              >
                <input
                  type="checkbox"
                  checked={!hidden.has(c.key)}
                  disabled={locked}
                  onChange={() => onToggle(c.key)}
                />
                {c.label}
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}
