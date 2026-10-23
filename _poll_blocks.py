"""Source blocks inserted by apply_ticket_polling.py."""

# ---------------------------------------------------------------------------
# hooks/useApiList.ts
# ---------------------------------------------------------------------------
#
# The existing refetch keeps its exact signature -- `() => void` -- because
# roughly twenty pages call it, several of them straight from an onClick.
# A quiet variant is added beside it rather than an argument added to it,
# so nothing already written can be affected by this at all.

USE_API_LIST_OLD = '''  const refetch = useCallback(() => {
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
'''

USE_API_LIST_NEW = '''  const load = useCallback(
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
          setItems(res.data.results);
          setCount(res.data.count);
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
'''

USE_API_LIST_RETURN_OLD = '''  return { items, count, loading, error, refetch, setItems };
'''

USE_API_LIST_RETURN_NEW = '''  return { items, count, loading, error, refetch, refetchQuietly, setItems };
'''

# ---------------------------------------------------------------------------
# pages/admin/TicketsPage.tsx
# ---------------------------------------------------------------------------

TICKETS_IMPORT = '''import { usePolling } from "../../hooks/usePolling";
'''

TICKETS_DESTRUCTURE_OLD = '''  const { items, loading, refetch } = useApiList<Ticket>(
'''

TICKETS_DESTRUCTURE_NEW = '''  const { items, loading, refetch, refetchQuietly } = useApiList<Ticket>(
'''

TICKETS_POLL = '''  // Tickets now arrive from the Conecto app by webhook, so this list goes
  // stale while somebody is looking straight at it -- and nothing on the
  // page gives them any reason to press refresh. Twenty seconds, quiet,
  // and only while the tab is in front. See usePolling.
  usePolling(refetchQuietly, 20_000);

'''

# ---------------------------------------------------------------------------
# pages/admin/TicketDetailPage.tsx
# ---------------------------------------------------------------------------

DETAIL_IMPORT = '''import { usePolling } from "../../hooks/usePolling";
'''

DETAIL_LOAD_OLD = '''  function load() {
    if (!id) return;
    api.get<Ticket>(`/tickets/${id}/`).then((res) => setTicket(res.data));
    // Reloaded alongside the ticket rather than once on mount: changing
    // the status or leaving a note is itself an audited event, and a
    // history panel that still shows the state before the thing you just
    // did reads as broken.
    setHistoryLoading(true);
'''

DETAIL_LOAD_NEW = '''  // `quiet` is for the background poll: same fetch, but without raising
  // the history panel's loading flag, which would otherwise blank a
  // readable panel every fifteen seconds.
  //
  // Note what is NOT reloaded here: `message`. The reply someone is
  // halfway through typing lives in its own state and is never touched by
  // a refresh, which is the one thing a polling page must not get wrong.
  function load(quiet = false) {
    if (!id) return;
    api.get<Ticket>(`/tickets/${id}/`).then((res) => setTicket(res.data));
    // Reloaded alongside the ticket rather than once on mount: changing
    // the status or leaving a note is itself an audited event, and a
    // history panel that still shows the state before the thing you just
    // did reads as broken.
    if (!quiet) setHistoryLoading(true);
'''

DETAIL_POLL = '''
  // A customer replying in the Conecto app appends to this conversation
  // with nothing on this page to announce it. Faster than the list: an
  // agent reading a thread is the person most likely to be answered while
  // they are still looking at it.
  usePolling(() => load(true), 15_000);
'''
