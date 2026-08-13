import type { ReactNode } from "react";

export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] shadow-sm">
      <table className="w-full text-sm">{children}</table>
    </div>
  );
}

export function THead({ children }: { children: ReactNode }) {
  return <thead className="border-b border-[var(--border-hairline)] bg-black/[0.02] text-left">{children}</thead>;
}

export function TH({ children }: { children?: ReactNode }) {
  return <th className="px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">{children}</th>;
}

/**
 * A clickable column header for server-side sorting. `field` is the API's
 * `ordering` field name; `ordering` is the current ordering string (e.g.
 * "full_name" or "-full_name") so the header can show the active direction.
 * Clicking cycles: ascending -> descending -> unsorted.
 */
export function SortableTH({
  field,
  ordering,
  onSort,
  children,
}: {
  field: string;
  ordering: string;
  onSort: (field: string) => void;
  children?: ReactNode;
}) {
  const isActive = ordering === field || ordering === `-${field}`;
  const direction = ordering === `-${field}` ? "desc" : "asc";
  return (
    <th
      onClick={() => onSort(field)}
      className={`cursor-pointer select-none px-4 py-2.5 text-xs font-semibold uppercase tracking-wide hover:text-[var(--text-primary)] ${
        isActive ? "text-[var(--text-primary)]" : "text-[var(--text-muted)]"
      }`}
    >
      <span className="inline-flex items-center gap-1">
        {children}
        <span className="text-[10px]">{isActive ? (direction === "asc" ? "▲" : "▼") : ""}</span>
      </span>
    </th>
  );
}

export function TR({ children, onClick }: { children: ReactNode; onClick?: () => void }) {
  return (
    <tr
      onClick={onClick}
      className={`border-b border-[var(--border-hairline)] last:border-0 ${onClick ? "cursor-pointer hover:bg-black/[0.02]" : ""}`}
    >
      {children}
    </tr>
  );
}

export function TD({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <td className={`px-4 py-2.5 text-[var(--text-primary)] ${className}`}>{children}</td>;
}
