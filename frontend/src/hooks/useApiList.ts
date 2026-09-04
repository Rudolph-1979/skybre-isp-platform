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

  const refetch = useCallback(() => {
    const id = ++requestId.current;
    setLoading(true);
    api
      .get<Paginated<T>>(url)
      .then((res) => {
        if (id !== requestId.current) return;
        setItems(res.data.results);
        setCount(res.data.count);
        setError(null);
      })
      .catch(() => {
        if (id !== requestId.current) return;
        setError("Failed to load data.");
      })
      .finally(() => {
        if (id !== requestId.current) return;
        setLoading(false);
      });
  }, [url]);

  useEffect(() => {
    refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  return { items, count, loading, error, refetch, setItems };
}
