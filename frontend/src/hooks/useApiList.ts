import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Paginated } from "../types";

export function useApiList<T>(url: string) {
  const [items, setItems] = useState<T[]>([]);
  const [count, setCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Which request is the current one. Two fetches can be in flight at
  // once -- a debounced search that fired twice, a double-clicked Next,
  // a filter changed mid-load -- and they resolve in whatever order the
  // network gives back, not the order they were sent.
  //
  // Without this the hook set state unconditionally, so the LAST response
  // to arrive won regardless of which query it answered: type "smit" then
  // "smith", and if the first reply lands second the table shows the
  // "smit" result set under the "smith" query and stays wrong until the
  // next keystroke. Same for page 2's rows rendering under "Page 3 of 32".
  // Twenty list pages share this hook.
  //
  // A monotonic id rather than an AbortController: the response is still
  // worth having in the HTTP cache, and this stays correct even if a
  // caller triggers refetch() by hand while another load is running.
  // UsageReportPage, OfflineCustomersPage and CustomerUsageCard all
  // implement the same guard locally, which is where the pattern is from.
  const requestId = useRef(0);

  const load = useCallback(
    (quiet: boolean) => {
      const id = ++requestId.current;
      // A quiet load is a background poll. The rows on screen are still
      // valid, so raising `loading` would replace a readable table with
      // "Loading…" every few seconds, and a brief network failure would
      // put an error where working data used to be. Neither is an
      // improvement on simply trying again at the next tick.
      if (!quiet) setLoading(true);
      api
        .get<Paginated<T>>(url)
        .then((res) => {
          if (id !== requestId.current) return;
          // An endpoint that answers with a bare array rather than
          // {count, results} used to set items to undefined, and the first
          // .filter() or .map() on it threw -- which React answers by
          // unmounting the whole tree. A white screen, no error on the
          // page, and a perfectly normal 200 in the network tab.
          //
          // That is a disproportionate punishment for a view that forgot
          // to paginate, and the page it happens on is not the page that
          // made the mistake. Degrade to the rows we were given instead.
          const body = res.data as unknown;
          if (Array.isArray(body)) {
            setItems(body as T[]);
            setCount(body.length);
          } else {
            setItems(res.data.results ?? []);
            setCount(res.data.count ?? 0);
          }
          setError(null);
        })
        .catch(() => {
          if (id !== requestId.current) return;
          if (!quiet) setError("Failed to load data.");
        })
        .finally(() => {
          if (id !== requestId.current) return;
          if (!quiet) setLoading(false);
        });
    },
    [url]
  );

  const refetch = useCallback(() => load(false), [load]);

  // For usePolling. Fetches without touching `loading` or `error`, so a
  // poll is invisible until it actually changes something on screen.
  const refetchQuietly = useCallback(() => load(true), [load]);

  useEffect(() => {
    refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  return { items, count, loading, error, refetch, refetchQuietly, setItems };
}
