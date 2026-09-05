# Outstanding items

_Last updated: 2026-09-04_

Nothing but the to-do list lives here. For what the project is, its full
history, and how it's deployed, see `PROJECT_STATUS.md` — that's the other
of the two `.md` files this repo maintains.

## Needs a decision or confirmation from Rudolph

- [ ] **Confirm the bank feeds deploy actually succeeded** — unlike
  recurring billing ("Deplyed sucessfully"), there's been no explicit
  confirmation yet for `skybre-bank-feeds.zip`.
- [ ] **Real FNB bank API access** — `bankfeeds/fnb_client.py` is an
  unverified placeholder (built without access to FNB's real API docs).
  Needs real credentials/documentation before it can be trusted; once
  available, only that one file should need to change.
- [ ] **Off-site (GCS) backup copies (Backup Phase 2)** — pending Rudolph
  creating a Google Cloud bucket + service account key. See
  `PROJECT_STATUS.md` section 10 for the exact steps once he's ready.
- [ ] **Turn on Xneelo's own VPS-level snapshots**, if their control panel
  offers them — an independent safety net alongside the app-level Postgres
  backups.
- [ ] **Any remaining Splynx "list N" export files** — the 1,592 customers
  imported so far came from one partial/filtered export
  ("Skybre Customers list 7.csv"); if more exist, re-importing is safe
  (duplicate detection via Splynx Portal login).
- [ ] **Auto-suspension** — deliberately left disabled in the recurring
  billing engine even though the logic exists (`_process_blocking`).
  Revisit once recurring billing has been running smoothly for a while.
- [ ] **Mikrotik hardware side of the RADIUS/OVPN setup** — the
  `mikrotik_teraco_jhb.rsc` RouterOS script has never been tested against
  the real device at Teraco JHB. Review and test on a maintenance window.
- [ ] **Uptime monitoring** — nothing currently alerts if the platform goes
  down; a free service like UptimeRobot would close this gap.
- [ ] **Disaster-recovery runbook** — the steps exist (see
  `PROJECT_STATUS.md` section 11) but have not been written down as a
  rehearsed runbook or actually practiced once.

## Small, low-urgency cleanup (no decision needed, just hasn't been done)

- [x] Remove the static "Demo logins" hint box from the live login page.
  Already gone — 2542b62 ("Catch the repo up to build 43") removed it and
  this entry was never ticked. `LoginPage.tsx` carries no hint box now.
- [x] Raise the password minimum length from 6 to 10+ characters
  (`AUTH_PASSWORD_VALIDATORS` in settings). Done 2026-09-04 — 10, and it
  turned out not to be a one-line change: the validators only ran on the
  public forgot-password screen, so the admin path that creates staff
  logins was checking length and nothing else. Both paths run all four
  rules now.
- [ ] Turn on two-factor authentication on Rudolph's own admin account
  (the feature has existed platform-wide since 2026-08-13, just not
  enabled on this one account).
- [ ] Turn imported customers' Splynx plan info (currently just text in
  each customer's Notes field) into real Tariff/Service links.

## From the 2026-09-04 platform audit — all fixed

Every finding below was reproduced first, fixed with a test that fails
against the pre-fix code, and verified against the full suite. Done
2026-09-05. Listed here rather than deleted so there is a record of what
was wrong, since several were silent and none had ever shown up as a
complaint.

- [x] Tax invoices did not add up — the PDF rounded VAT per line while the
  invoice rounded once, so the exclusive + VAT a customer entered in their
  own books missed the total they were billed.
- [x] Output VAT on a converted quote was declared in **no** VAT return —
  dated by `date_created`, which never moves off the day the quote was
  raised. `Invoice.date_issued` now records when a document actually
  becomes a tax invoice.
- [x] Expenses claimed 15% input VAT on wages, where none was ever
  charged.
- [x] UIF was capped once per run instead of once per month, so a
  multi-month catch-up run under-deducted and under-declared on the EMP201.
- [x] Recalculating a draft payroll run deleted a leaver's line, and their
  final pay with it.
- [x] Recording a payment overwrote any credit approved or invoice issued
  in the same moment, and a second payment against one invoice erased the
  first. The forward path was the last half of the ledger still doing
  read-modify-write.
- [x] A quote could be PATCHed straight to `unpaid`, becoming a live tax
  invoice still numbered QUO-, with the quoted tariff never turned into a
  service.
- [x] Changing a product's Tracking dropdown zeroed its stock on hand; a
  resell-price typo 500'd the whole Products API; two simultaneous stock
  issues could both pass the availability check.
- [x] A mistyped service odometer was permanent and left the vehicle
  reading Overdue for good, while a late logbook entry could never be
  recorded at all.
- [x] Scheduling could only book jobs for the first 500 customers, and
  swallowed every save and delete error in silence.

And the five that were still unverified leads when the list was written —
all five turned out to be real:

- [x] Any staff member could pull **any** customer's statement or invoice
  PDF, ignoring their reseller restriction.
- [x] Support tickets, and their whole comment threads, were visible and
  editable across partners.
- [x] The speed-now endpoint leaked another reseller's plan, live line
  speed and month-to-date usage.
- [x] Fair-use shaping and speed-window boosts were pushed only to the
  live session and never into `radreply`, so a router reboot restored the
  old speed permanently — and the scheduled job then reported "already
  correct" and never re-sent it.
- [x] (the payment race, above)

## Not yet installed (the code exists, the cron entry doesn't)

- [ ] `sync_bank_feeds` management command — documented crontab line:
  `0 * * * * cd /home/ubuntu/isp-platform && docker compose exec -T backend python manage.py sync_bank_feeds`
  Low urgency until real FNB API access exists.
- [ ] `run_recurring_billing` management command — documented crontab line:
  `0 6 * * * cd /home/ubuntu/isp-platform && docker compose exec -T backend python manage.py run_recurring_billing`
  Currently run manually via Finance → Recurring Billing → Preview/Run
  instead, by design, until that's proven out.

## Housekeeping

- [x] Consolidate every `.md` file in this repo down to exactly two:
  `PROJECT_STATUS.md` and this file. Done 2026-08-18 — `README.md`,
  `DEPLOYMENT.md`, `BACKUP.md`, `RADIUS_SETUP.md`,
  `SECURITY_AND_REDUNDANCY.md`, `BANK_FEEDS.md`, and
  `DEPLOY_quote_proforma_invoicing.md` were folded into `PROJECT_STATUS.md`
  and removed.
