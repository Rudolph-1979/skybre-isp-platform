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

## Four findings from the first audit that were never acted on

Found 2026-09-06 by reconciling every finding ever raised against the files
the branch actually changed. These were in the first audit's results, were
never verified, never fixed, and — worse — were never written down here.
That is the third time this tracking gap has bitten; the reconciliation
script is the fix, and the list below is the result.

- [ ] **[high]** A billing run done a day late permanently shifts the
  customer's billing date forward, and the next month's on-time run then
  skips them — a whole month never invoiced. `billing/recurring.py:214`
- [ ] **[medium]** Anniversary billing reads the day-of-month off
  `created_at` in **UTC**, so a customer created just after midnight SAST
  is billed on the wrong day; and an exact `==` on day-of-month means a due
  day of 29/30/31 never matches in a shorter month, so no invoice is raised
  at all. `billing/recurring.py:155`
- [ ] **[medium]** Approving a leave request twice deducts the days twice;
  a failure between the two writes deducts the days while leaving the
  request pending. `payroll/views.py:336`
- [ ] **[medium]** A finalized payroll run's period is still writable, and
  a partial PATCH of one date raises `KeyError` → HTTP 500.
  `payroll/serializers.py:171`

All four are being verified now. None should be acted on before that.

## Network / RADIUS — where it actually stands (2026-09-06)

Fixed and verified by probe: the SNMP community is no longer readable
through the device API *or* the audit trail; speed-now is partner-scoped;
a speed change reaches `radreply` so it survives a reconnect, and a line
whose heartbeat merely lapsed is no longer skipped; a suspended PPPoE
customer is blocked on the walled-garden address they are actually using
rather than their recycled old one; the device list no longer loads the
whole monitoring history.

**Still open on the network side:**

- [ ] **[unverified]** A line that was offline when a speed window opened
  may never get its static shaper queue re-synced, so the boost is
  swallowed. `apply_speed_policies.py:181`
- [ ] **[unverified]** `--limit` may no longer cap anything for offline
  lines, and those writes are invisible in both the dry run and the log.
  `apply_speed_policies.py:187`
- [ ] **Nothing here has run against real MikroTik hardware.** Every CoA,
  shaper and blocking test is against a FakeNAS. See the Teraco item
  above, which says the same thing about the RouterOS script.

Two of the fixes above were findings from the FIRST audit that I recorded
nowhere and nearly lost -- the block-list address and the monitoring
prefetch. Both are now done, but the lesson is in the list, not the code.

## Self-review of this branch — 18 claims still unchecked (2026-09-06)

The branch's own diff was reviewed adversarially for regressions it
introduced. 29 claims came back. **11 have now been reproduced and dealt
with** — see the four "found reviewing its own diff" commits. Among them
were three blockers: UIF charged twice on an ordinary 26th-to-25th pay
cycle, a rounded workshop odometer throwing away the whole service, and a
line that only *looked* offline never getting its speed change.

Two of the eleven were closed as "cannot be prevented, but is now
recoverable and honestly documented" rather than fixed outright: an
upward odometer typo (450000 for 45000) cannot be told from a genuine
large jump without inventing a threshold, so both the reading and the
service record are now deletable instead. A vehicle left holding a bogus
high service record still reads a calm "ok" rather than announcing the
contradiction — known, not fixed.

**The 18 below have NOT been verified.** Treat each as a lead: of the
eleven checked so far, all were real, but the checking is what makes them
actionable — and the earlier round of this exercise found roughly a third
of its claims to be wrong. Nothing here should be acted on without
reproducing it first.

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
- [ ] **[serious]** Picking Salaries latches the VAT rate to 0 and nothing ever puts it back
  `frontend/src/pages/admin/AccountantPage.tsx:620` — wrong_business_rule
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
