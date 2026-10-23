# Conecto ↔ Vexar (Skybre) — the Vexar side

Self-contained `conecto` app. The only existing files touched are
`config/settings.py` and `config/urls.py`, and those are edited in place
by `apply_conecto.py` rather than overwritten.

## What Conecto must be told

Our three webhook URLs (note the `/api/` prefix — nginx only proxies
`/api/` to Django, so a URL without it never reaches this code, and the
trailing slash matters because Django's APPEND_SLASH redirect turns a
POST into a GET and drops the body):

    POST https://<host>/api/webhooks/conecto/ticket-created/
    POST https://<host>/api/webhooks/conecto/ticket-closed/
    POST https://<host>/api/webhooks/conecto/client-registered/

All three require `Authorization: Bearer <CONECTO_WEBHOOK_TOKEN>`.

## Configuration (.env)

    CONECTO_API_KEY=...            # outbound; blank = integration off
    CONECTO_WEBHOOK_TOKEN=...      # inbound; falls back to CONECTO_API_KEY
    CONECTO_URL_HEALTH=...
    CONECTO_URL_REGISTER_CLIENT_CODE=...
    CONECTO_URL_CREATE_TICKET=...
    CONECTO_URL_UPDATE_TICKET=...
    CONECTO_URL_SEND_ALERT=...
    CONECTO_URL_REGISTER_TOWN=...

Optional: `CONECTO_TIMEOUT_SECONDS` (6), `CONECTO_WEBHOOK_RATE` (120/min).

**Blank `CONECTO_API_KEY` means nothing is sent.** That is the safe
default for a staging copy or a rebuild that happens before the .env is
updated — it fails closed, silently, rather than posting live customer
records at whatever URLs it inherited.

## Rollout order — this matters

1. `python manage.py conecto_register_towns` (dry run, check the
   spellings), then `--apply`.
2. `python manage.py conecto_sync_clients` (dry run), then `--apply`.
3. Only then tell subscribers the app is live.

Conecto matches a client's town against its registered towns **exactly
and case-sensitively**, and when it doesn't match it stores the client
with no town and still answers 200. So a town registered in the wrong
order or with a stray space produces no error anywhere — just a
subscriber who never receives an outage alert. And Conecto refuses to
register a subscriber whose client code it has never seen, so every
customer who predates this app cannot sign up until step 2 has run.

## Recovering from failures

Nothing here is allowed to break the thing that triggered it. A failed
push is recorded in `ConectoCall` (Django admin → Conecto calls) with
its reason, and replayed by:

    python manage.py conecto_resync                # report
    python manage.py conecto_resync --apply

Worth running on a schedule. Conecto has no retry queue of its own —
their spec says a failed webhook is logged and dropped — so the
durability lives here or nowhere.

## What is deliberately lossy

Skybre has four ticket statuses and four priorities; Conecto has three
and two. `mapping.py` documents every choice. The one to know:
**Resolved and Closed both go out as "closed", and "closed" comes back
as Closed** — so a Resolved ticket that round-trips becomes Closed. An
unrecognised status coming in is *ignored*, not defaulted, because
defaulting to Open would let a typo reopen a finished ticket.

## Not built yet

`client.send_alert()` exists and works, but nothing calls it — there is
no outage/maintenance screen in Skybre to trigger it from. That is the
obvious next piece: a form that picks a town and pushes a notice to
every subscriber in it.

---

## Job Dispatch (added 16 Sep 2026)

Two more webhook URLs to give Conecto, same bearer token:

    POST https://<host>/api/webhooks/conecto/job-status-changed/
    POST https://<host>/api/webhooks/conecto/job-checklist-completed/

Outbound, Skybre now calls `scheduleJob` and `setStockChecklist`. Those
two URLs are **built in** rather than read from `.env` — Conecto
published them outright, and adding two keys to a settings dict that is
already deployed is more risk than a documented default. `CONECTO_URLS`
still overrides them if that ever changes. Nothing is sent while
`CONECTO_API_KEY` is blank, exactly as before.

### Technician codes

Conecto issues them; Skybre only records who they belong to.

    python manage.py conecto_link_technician --list
    python manage.py conecto_link_technician christo TECH-001
    python manage.py conecto_link_technician christo --remove

**A job assigned to somebody with no code here is never sent.** Silently
— there is nowhere to send it. That is the first thing to check when a
technician says their phone is empty.

### What pushes, and when

Saving a job assigned to a linked technician puts it on their phone, and
a reschedule, reassignment or change of address follows it. A save that
changes nothing they would see pushes nothing, so an unrelated edit is
not a notification.

**Conecto has no endpoint to withdraw a job.** Cancel one, or move it to
another technician, and the original still has it on their list — the
log says so each time it happens, but somebody has to tell them. Worth
raising with Conecto.

### Stock checklists

    python manage.py conecto_set_checklist christo "Router (dual-band)" "Ladder"
    python manage.py conecto_set_checklist christo ... --apply
    python manage.py conecto_set_checklist --all "Router" "Ladder" --apply
    python manage.py conecto_set_checklist christo --date 2026-09-17 --from-yesterday --apply

A technician cannot start their first job until they submit the list, so
**a technician with no list set cannot start work.** Setting it again for
the same day replaces it.

`any_missing` on the completion is the one to watch: it means they went
out having ticked past something they did not have. It is logged as a
warning and stored on the checklist row.

### Where everyone is

Not built, by choice — every status update, position, kilometre reading
and time on site is stored in `ConectoJobEvent` as it arrives, so the
screen can be built later against real history rather than starting from
an empty table. Django admin → Conecto job events is the current view.

### Known gap: no coordinates

`scheduleJob` takes lat/lng and we send none, because nothing on this
platform stores coordinates for a customer or a job. Conecto geocodes the
address instead, which is fine in a town and poor on a farm road. If
technicians report being sent to the wrong place, a lat/lng on the
customer is the fix.
