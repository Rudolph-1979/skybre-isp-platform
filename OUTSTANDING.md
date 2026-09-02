# Outstanding items

_Last updated: 2026-08-18_

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

- [ ] Remove the static "Demo logins" hint box from the live login page —
  cosmetic, but shows credentials that don't exist in this deployment.
- [ ] Raise the password minimum length from 6 to 10+ characters
  (`AUTH_PASSWORD_VALIDATORS` in settings) — a one-line change.
- [ ] Turn on two-factor authentication on Rudolph's own admin account
  (the feature has existed platform-wide since 2026-08-13, just not
  enabled on this one account).
- [ ] Turn imported customers' Splynx plan info (currently just text in
  each customer's Notes field) into real Tariff/Service links.

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
