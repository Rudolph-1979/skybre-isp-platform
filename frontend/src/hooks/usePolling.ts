import { useEffect, useRef } from "react";

/**
 * Re-run `callback` on an interval, but only while the tab is in front.
 *
 * WHY THIS EXISTS
 *
 * Some data arrives without anybody in this app doing anything: a ticket
 * raised in the Conecto app, a customer's reply to a thread a support
 * agent is reading right now. A page that only fetches on mount shows
 * none of it, and the agent has no way to know there is anything to
 * refresh for -- so they answer into a conversation that has already
 * moved on, or leave a new ticket sitting unseen on a screen that looks
 * complete.
 *
 * WHY NOT WEBSOCKETS
 *
 * They would be the right answer for a chat product. This backend has no
 * channels layer, so adding one means a new process, a new dependency in
 * the deploy, and a new thing that can be down -- for a handful of staff
 * looking at a ticket list. Polling on a visible tab costs one request
 * every twenty seconds per open page and is deleteable in an afternoon if
 * that ever changes.
 *
 * WHY VISIBILITY MATTERS MORE THAN THE INTERVAL
 *
 * Staff leave tabs open for days. Without the visibility check, every
 * abandoned tab in the building keeps polling until the laptop is shut,
 * and the load is proportional to how forgetful people are rather than to
 * how many of them are working. Hidden tabs are stopped entirely, and
 * coming back to a tab fetches immediately rather than showing stale rows
 * until the next tick -- which is the moment the data is most obviously
 * wrong, because the person has just looked at it again.
 *
 * The callback should be a QUIET refetch: one that does not flip a
 * loading flag. A poll that replaces a readable table with "Loading…"
 * every twenty seconds is worse than not polling at all.
 */
export function usePolling(callback: () => void, intervalMs: number, enabled = true) {
  // Kept in a ref so a caller passing an inline arrow -- which is a new
  // function on every render -- does not tear down and rebuild the timer
  // on every render, which would mean it effectively never fires.
  const saved = useRef(callback);
  useEffect(() => {
    saved.current = callback;
  }, [callback]);

  useEffect(() => {
    if (!enabled) return;

    let timer: number | undefined;

    const stop = () => {
      if (timer !== undefined) window.clearInterval(timer);
      timer = undefined;
    };

    const start = () => {
      stop();
      timer = window.setInterval(() => {
        // Checked here as well as on visibilitychange: some browsers
        // throttle rather than stop background timers, and a throttled
        // tick is still a request nobody is going to look at.
        if (document.visibilityState === "visible") saved.current();
      }, intervalMs);
    };

    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        saved.current();
        start();
      } else {
        stop();
      }
    };

    if (document.visibilityState === "visible") start();
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [intervalMs, enabled]);
}
