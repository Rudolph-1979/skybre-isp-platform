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

## From the 2026-09-04 platform audit (confirmed, not yet fixed)

Each was reproduced by reading the code and independently checked from
three angles (is it reachable in production / can the mechanism be
refuted / is it already handled or tested). The three that were fixed
are in the branch already; these are the rest, roughly worst first.

- [ ] **Invoice/quote PDFs do not add up** (`notifications/pdf.py:132`) —
  the PDF rounds VAT per line while the stored invoice rounds once, so a
  tax invoice's own columns disagree with its total, and with what the VAT
  return declares. Customer-facing.
- [ ] **VAT return dates a converted quote by the quote's date**
  (`expenses/views.py:62`) — output VAT can land in an already-filed
  period and never be declared.
- [ ] **Expenses default to claiming 15% input VAT** (`expenses/models.py:64`),
  including on Salaries/Payroll, where no VAT was ever charged.
- [ ] **UIF is capped at one month's ceiling** however many months a
  payroll run covers (`payroll/services.py:128`), so a catch-up run
  under-deducts and under-remits.
- [ ] **Recalculating a draft payroll run deletes the line of anyone
  deactivated since it was generated** (`payroll/services.py:89`) — final
  pay and typed PAYE vanish with no warning.
- [ ] **Changing a product's "Tracking" dropdown zeroes its stock on hand**
  (`inventory/serializers.py:86`) — the receipt-import path guards against
  exactly this and says why; the ordinary product edit does not.
- [ ] **A resell-price typo 500s the whole Products API**
  (`inventory/serializers.py:69`), blanking the Inventory tab and every
  product picker.
- [ ] **Two simultaneous stock issues can both pass the availability
  check** (`inventory/serializers.py:636`), driving stock negative.
- [ ] **A quote PATCHed straight to "unpaid"** becomes a live tax invoice
  still numbered QUO-, and its quoted tariff never becomes a service
  (`billing/serializers.py:314`).
- [ ] **A mistyped odometer on "Mark as serviced"** leaves the vehicle
  reading Overdue permanently, with no way to correct it from the UI
  (`fleet/views.py:50`); backdated readings are refused outright
  (`fleet/serializers.py:34`), so a late logbook entry can never be
  recorded.
- [ ] **Scheduling page, front end** (`SchedulingPage.tsx`): the job
  customer dropdown silently lists only the first 500 of ~1,592 customers
  (line 80), and saving or deleting a job/shift swallows every API error
  so a rejected save reports nothing (line 240).

Also flagged but NOT yet verified, so treat as leads rather than facts —
the audit ran out of budget partway through checking them: statement/
invoice PDFs may not be partner-scoped (`notifications/views.py:147`),
tickets may not be partner-scoped (`tickets/views.py:22`), the speed-now
endpoint may leak another reseller's line speed and usage
(`radiusauth/views.py:618`), recording a payment updates the balance with
a read-modify-write that a concurrent debit could lose
(`billing/serializers.py:507`), and fair-use shaping may be pushed only to
the live session and never into radreply, so a router reboot restores the
old speed (`radiusauth/.../apply_speed_policies.py:158`).

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
