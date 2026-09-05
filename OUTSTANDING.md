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

## Self-review of this branch — UNVERIFIED claims (2026-09-06)

After the audit fixes were written, the branch's own diff was reviewed
adversarially by readers who had not written it. It found 29 claimed
defects **introduced by the fixes themselves**. Three were reproduced and
fixed (see the commit "Fix three defects this branch introduced"); the
worst of them charged UIF twice on an ordinary 26th-to-25th pay cycle.

**The remaining 26 below were NOT verified.** The verification stage ran
out of budget before it could confirm or refute a single one, so treat
every line as a LEAD, not a fact — roughly a third of the claims that did
get checked turned out to be wrong. Several claim to have been probed
live by the reviewer, which makes them worth checking first, but nobody
has independently confirmed any of them.

Do not push this branch to production until these have been worked
through. The three that were real were real enough to take money off
staff.

- [ ] **[blocker]** mark_serviced now refuses the whole service when the workshop odometer is below an earlier reading — vehicle stays stuck "In Service"
  `backend/fleet/views.py:90` — regression
- [ ] **[blocker]** Offline branch marks last_pushed_rate_limit delivered, permanently suppressing the CoA for a session that only *looked* offline
  `backend/radiusauth/management/commands/apply_speed_policies.py:180` — regression
- [ ] **[serious]** Backfill stamps cancelled QUOTES as issued tax invoices, re-creating the exact VAT gap the change fixes
  `backend/billing/migrations/0021_invoice_date_issued.py:22` — migration_safety
- [ ] **[serious]** A NULL date_issued is silently invisible to every VAT return, and the one-shot backfill runs while the old code is still writing invoices
  `backend/billing/models.py:367` — migration_safety
- [ ] **[serious]** Recalculate can no longer take anyone off a draft run, and there is no other way to remove a line
  `backend/payroll/services.py:158` — regression
- [ ] **[serious]** UIF splits remuneration by calendar days rather than by what was actually earned in each month
  `backend/payroll/services.py:92` — wrong_business_rule
- [ ] **[serious]** The new UIF tests assert the code's own output, so they lock in the part-month ceiling error
  `backend/payroll/test_uif_period.py:82` — weak_test
- [ ] **[serious]** The 450000-for-45000 typo the diff says it fixes is still accepted and still poisons the vehicle permanently
  `backend/fleet/views.py:82` — weak_test
- [ ] **[serious]** _service_moment files a backdated service at 23:59:59 of its day, so it is checked against readings taken after the vehicle left the workshop
  `backend/fleet/views.py:29` — regression
- [ ] **[serious]** Tracking type can no longer be corrected on a product whose stock is already zero
  `backend/inventory/serializers.py:126` — regression
- [ ] **[serious]** A line that was offline when a speed window opened never gets its static shaper queue re-synced, so the boost is silently swallowed
  `backend/radiusauth/management/commands/apply_speed_policies.py:181` — regression
- [ ] **[serious]** --limit no longer caps anything for offline lines, and the writes are invisible in both dry run and log
  `backend/radiusauth/management/commands/apply_speed_policies.py:187` — regression
- [ ] **[serious]** allow_null=False is a no-op on the deletion-request queryset — the comment states a guarantee the SQL does not give
  `backend/billing/views.py:387` — misapplied_edit
- [ ] **[serious]** Deletion requests are scoped on read/approve but not on create, so a restricted user can still reach a competitor's quote
  `backend/billing/views.py:374` — wrong_business_rule
- [ ] **[serious]** Picking Salaries latches the VAT rate to 0 and nothing ever puts it back
  `frontend/src/pages/admin/AccountantPage.tsx:620` — wrong_business_rule
- [ ] **[serious]** Correcting a workshop odometer typo leaves the ServiceRecord at the typo, so the vehicle never comes due for service again
  `frontend/src/pages/admin/VehicleDetailPage.tsx:175` — wrong_business_rule
- [ ] **[serious]** SNMP community made write-only on /api/devices/ but still returned in plaintext by /api/audit-events/
  `backend/network/serializers.py:49` — misapplied_edit
- [ ] **[minor]** New "concurrency" tests are single-threaded and cannot fail if the lock and the atomic block are removed
  `backend/billing/test_payment_ledger.py:229` — weak_test
- [ ] **[minor]** date_issued was wired into the tax invoice and the VAT return only, so one invoice now carries two conflicting dates and the return cannot be reconciled
  `backend/notifications/templates/notifications/statement_pdf.html:35` — misapplied_edit
- [ ] **[minor]** test_a_bandwidth_expense_still_defaults_to_15 never exercises the default, and no test in the file omits the rate
  `backend/expenses/test_salary_vat.py:91` — weak_test
- [ ] **[minor]** Two readings at the exact same instant are accepted as long as the new one is higher
  `backend/fleet/serializers.py:46` — wrong_business_rule
- [ ] **[minor]** The new "the product row is the lock" invariant is not honoured by the manual stock adjustment endpoint
  `backend/inventory/views.py:91` — transaction_lock
- [ ] **[minor]** handleDeleteReading has no catch — a refused delete says nothing and rejects unhandled
  `frontend/src/pages/admin/VehicleDetailPage.tsx:201` — regression
- [ ] **[minor]** Odometer history table gained two blank <TH> for one new <TD>
  `frontend/src/pages/admin/VehicleDetailPage.tsx:436` — misapplied_edit
- [ ] **[minor]** Password minimum raised to 10 server-side, but both password UIs still advertise 6 and 8
  `backend/config/settings.py:154` — regression
- [ ] **[minor]** No test pins the minimum at 10 -- the boundary test uses a 15-character password despite its name
  `backend/accounts/test_password_policy.py:151` — weak_test

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
