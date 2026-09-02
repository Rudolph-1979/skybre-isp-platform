import type { ReactNode, MouseEvent } from "react";

export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] shadow-sm">
      <table className="w-full text-sm">{children}</table>
    </div>
  );
}

export function THead({ children }: { children: ReactNode }) {
  return <thead className="border-b border-[var(--border-hairline)] bg-[var(--tint-subtle)] text-left">{children}</thead>;
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

export function TR({
  children,
  onClick,
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  // Appended, not replaced: the border and hover behaviour below are what
  // make a row look like a row, and a caller passing a class should not
  // silently lose them. TD has taken a className since it was written; TR
  // not doing so has already sent two features looking for a workaround.
  className?: string;
}) {
  return (
    <tr
      onClick={onClick}
      className={`border-b border-[var(--border-hairline)] last:border-0 ${
        onClick ? "cursor-pointer hover:bg-[var(--tint-subtle)]" : ""
      } ${className}`}
    >
      {children}
    </tr>
  );
}

export function TD({
  children,
  className = "",
  onClick,
}: {
  children: ReactNode;
  className?: string;
  // Escape hatch for a cell that needs to opt out of its row's own
  // onClick (e.g. a checkbox cell inside a row that otherwise navigates
  // on click) -- pass a handler that calls e.stopPropagation().
  onClick?: (e: MouseEvent<HTMLTableCellElement>) => void;
}) {
  return (
    <td className={`px-4 py-2.5 text-[var(--text-primary)] ${className}`} onClick={onClick}>
      {children}
    </td>
  );
}
