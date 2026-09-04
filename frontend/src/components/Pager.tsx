import { btnSecondary } from "./Modal";

/**
 * Previous / Page n of m / Next, with a "showing x-y of z" line.
 *
 * Extracted from CustomersPage, which was the only list in the app that
 * paged properly. Every Finance and Accountant list requested
 * `page_size=100` (or 200) and rendered no pager at all, while printing
 * the true `count` in its header -- so filtering invoices to "0-90 days
 * overdue" showed "340 documents" above a table of 100 rows, and the
 * other 240 overdue invoices were unreachable from the UI. Collections
 * was working a truncated list with nothing to say so.
 *
 * Renders nothing when everything fits on one page, so adding it to a
 * short list costs no visual noise.
 */
export function Pager({
  page,
  pageSize,
  count,
  shown,
  onPageChange,
  label = "rows",
}: {
  page: number;
  pageSize: number;
  count: number;
  /** How many rows are actually on screen. */
  shown: number;
  onPageChange: (page: number) => void;
  /** Plural noun for the summary line, e.g. "invoices". */
  label?: string;
}) {
  const totalPages = Math.max(1, Math.ceil(count / pageSize));
  if (count <= pageSize) return null;

  const first = shown === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = (page - 1) * pageSize + shown;

  return (
    <div className="mt-3 flex items-center justify-between text-sm text-[var(--text-muted)]">
      <span>
        Showing {first}–{last} of {count} {label}
      </span>
      <div className="flex gap-2">
        <button
          type="button"
          className={btnSecondary}
          disabled={page <= 1}
          onClick={() => onPageChange(Math.max(1, page - 1))}
        >
          Previous
        </button>
        <span className="px-2 py-2">
          Page {page} of {totalPages}
        </span>
        <button
          type="button"
          className={btnSecondary}
          disabled={page >= totalPages}
          onClick={() => onPageChange(Math.min(totalPages, page + 1))}
        >
          Next
        </button>
      </div>
    </div>
  );
}
