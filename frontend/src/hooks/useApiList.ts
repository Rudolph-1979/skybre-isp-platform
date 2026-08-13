import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { Paginated } from "../types";

export function useApiList<T>(url: string) {
  const [items, setItems] = useState<T[]>([]);
  const [count, setCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(() => {
    setLoading(true);
    api
      .get<Paginated<T>>(url)
      .then((res) => {
        setItems(res.data.results);
        setCount(res.data.count);
        setError(null);
      })
      .catch(() => setError("Failed to load data."))
      .finally(() => setLoading(false));
  }, [url]);

  useEffect(() => {
    refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  return { items, count, loading, error, refetch, setItems };
}
