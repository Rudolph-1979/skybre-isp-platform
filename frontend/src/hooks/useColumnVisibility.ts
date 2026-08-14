import { useEffect, useState } from "react";

/**
 * Persists which columns are hidden for a given table, keyed by a stable
 * per-page storage key so each list page remembers its own preferences
 * independently (stored in localStorage, survives reloads).
 *
 * `alwaysVisible` keys can never be hidden (e.g. the primary identifying
 * column of a table) — toggle() is a no-op for them and isVisible() always
 * returns true.
 */
export function useColumnVisibility(storageKey: string, alwaysVisible: string[] = []) {
  const fullKey = `columns:${storageKey}`;
  const [hidden, setHidden] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem(fullKey);
      if (!raw) return new Set();
      const arr = JSON.parse(raw) as string[];
      return new Set(arr.filter((k) => !alwaysVisible.includes(k)));
    } catch {
      return new Set();
    }
  });

  useEffect(() => {
    localStorage.setItem(fullKey, JSON.stringify([...hidden]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hidden]);

  function isVisible(key: string) {
    return alwaysVisible.includes(key) || !hidden.has(key);
  }

  function toggle(key: string) {
    if (alwaysVisible.includes(key)) return;
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return { hidden, isVisible, toggle };
}
