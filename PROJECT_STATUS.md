# Skybre ISP Platform — Project Status (single source of truth)

_Last updated: 2026-08-18_

> **Two-file convention (Rudolph's standing instruction, 2026-08-18):** this
> project now maintains exactly two `.md` files —
> **this one** (everything about the project: what it is, how it's built,
> how it's deployed, and its full history) and **`OUTSTANDING.md`**
> (nothing but the current to-do list). Every other `.md` file that used to
> live in this repo (`README.md`, `DEPLOYMENT.md`, `BACKUP.md`,
> `RADIUS_SETUP.md`, `SECURITY_AND_REDUNDANCY.md`, `BANK_FEEDS.md`,
> `DEPLOY_quote_proforma_invoicing.md`) has been folded into this file and
> removed. **If you're a new chat session picking this project up, read
> this whole file, then `OUTSTANDING.md` — that's the complete picture,
> no other doc should exist.**
>
> A note on provenance: the feature history below spans several sessions.
> Where this assistant has direct chat-by-chat detail (the Quote/Pro
> Forma/Invoice workflow, the deletion-request governance workflow,
> recurring billing, and bank feeds), the "why" and the decisions made are
> included. For the 2026-08-16 batch (RADIUS/Mikrotik integration, the
> Partner/reseller layer, payroll, fleet) this assistant is working from
> the actual codebase (models, migrations, docstrings, dedicated setup
> docs) rather than a first-hand record of that conversation — the *what*
> below is reliable, the *why it was decided that way* is not always
> available.

## 1. What this is

A Splynx-inspired ISP management platform — customer CRM, billing/invoicing,
network device & RADIUS/OVPN integration, a customer self-service portal,
support ticketing, scheduling, stock/inventory, payroll, and fleet
management — built for **Skybre Pty Ltd**'s real business use, not a demo.

Splynx is a mature commercial product built over many years by a dedicated
team; this project is a solid, working, real-data foundation covering
Skybre's actual day-to-day operations, not a line-for-line feature match.

## 2. Stack & architecture

- **Backend:** Django 5, Django REST Framework, Simple JWT, PostgreSQL,
  django-filter, Gunicorn, WhiteNoise
- **Frontend:** React 19 + TypeScript, Vite, Tailwind CSS v4, React Router,
  Recharts, Axios

```
isp-platform/
  backend/            Django project
    config/           settings, URLs, pagination, media security
    accounts/         custom User (role: admin/staff/technician/customer), JWT auth, 2FA, section permissions, partner visibility
    customers/        Customer CRM, Partner (reseller) model, deletion-request workflow
    billing/          Tariff, Service, Invoice/Quote/ProForma, Payment, CreditRequest, InvoiceDeletionRequest, recurring billing engine
    network/          Device, IPPool, IPAddress, MonitoringReading, ConnectionRule, RouterOS live API integration
    radiusauth/       RadCheck/RadReply/RadAcct (FreeRADIUS-schema-compatible), RadiusNasClient, OvpnSettings
    bankfeeds/        BankAccount, BankTransaction, BankFeedSyncLog, FNB client (placeholder), CSV import
    tickets/          Ticket, TicketComment
    scheduling/       Job, Shift (staff-only calendar)
    inventory/        Supplier, Product, StockReceipt, StockIssue
    payroll/          StaffProfile, AttendanceRecord, PayrollRun/Line, LeaveRequest
    fleet/            Vehicle, OdometerReading, ServiceRecord, FuelLog
    notifications/    EmailTemplate, EmailLog, EmailSettings (SMTP singleton)
  frontend/           React admin dashboard + customer portal (single app, role-based routing)
```

## 3. Current live state

- Deployed via Docker Compose on a Xneelo Cloud VPS.
- **VPS IP:** `154.65.111.61`. **Live domain:** `https://portal.skybre.co.za` (HTTPS live since 2026-08-14, Certbot-issued cert).
- **Access:** SSH as `ubuntu@154.65.111.61` using the `Skybre ISP` key pair
  (private key file `Skybre ISP.pem`, kept locally by Rudolph — not in this repo)
- **Project path on the VPS:** `/home/ubuntu/isp-platform`
- Stack: `db` (Postgres 16), `backend` (Django/Gunicorn), `frontend` (Nginx serving the React build + proxying `/api`), fronted by a host-level Nginx + Certbot for TLS.
- **1,592 real customers imported from Splynx** (see section 8). `SEED_DEMO_DATA=False` — no fictional demo data has ever been loaded against this deployment.
- Admin login: username `Rudolph`, real superuser, `role=admin`. Django's `createsuperuser` doesn't set this platform's custom `role` field, so any *new* superuser needs a manual fix immediately after creation:
  ```bash
  docker compose exec backend python manage.py shell -c \
    "from accounts.models import User; u = User.objects.get(username='NEWUSER'); u.role = 'admin'; u.save()"
  ```
- Code is backed up to GitHub — `github.com/Rudolph-1979/skybre-isp-platform` (private repo). This is a separate, occasional step, not the deploy mechanism — see section 9.
- `ufw` firewall enabled (22/80/443 only, IPv4+IPv6, default-deny otherwise) since 2026-08-14.
- `/media/...` uploads (e.g. stock receipt attachments) are only reachable via short-lived signed links generated at the moment an authenticated API response hands one out — a bare/guessed/stale URL gets a 403 (`config/media_security.py`).

## 4. Local development setup

```bash
# Backend
cd backend
python3 -m venv ../venv && source ../venv/bin/activate
pip install -r requirements.txt
createdb ispplatform            # matching backend/.env
cp .env.example .env
python manage.py migrate
python manage.py seed_demo_data                          # fictional demo data — dev only, never run against prod
python manage.py simulate_monitoring --hours 48 --interval-minutes 15
python manage.py runserver 0.0.0.0:8000
```

```bash
# Frontend
cd frontend
npm install
npm run dev     # http://localhost:5173, proxies /api to http://localhost:8000
```

**Demo logins** (created by `seed_demo_data`, dev/local only — never seeded against the real deployment): `admin`/`admin12345` (Admin), `jsupport`/`staff12345` (Staff, support), `nbilling`/`staff12345` (Staff, billing), `ttech`/`staff12345` (Technician), `cust000`…`cust0XX`/`customer12345` (Customer, ~60% of seeded customers get a login). Staff/admin land on `/admin`; customers land on `/portal`, enforced at the API layer too — a customer JWT can only ever read their own invoices/services/tickets (verified with 403s during testing), never staff-only data.

**Known cosmetic issue, not yet fixed:** the live login page still shows a static "Demo logins" hint box referencing accounts that don't exist in this deployment. Harmless but should be removed before more customers see it — tracked in `OUTSTANDING.md`.

## 5. What's simulated vs. real

Everything is real **except** network monitoring readings.
`network.MonitoringReading` rows are generated by `python manage.py
simulate_monitoring`, which writes realistic-looking latency/bandwidth/CPU
data per device on a timer — there's no physical network being polled by
that command. Separately, individual `Device`s that have `api_enabled=True`
*do* talk to a real RouterOS API for live sessions/poll-now/test-connection/
disconnect-session (see feature history, 2026-08-16) — that path is real
where configured; `simulate_monitoring` is the fallback/demo path for
devices that aren't wired up that way.

To wire real SNMP-based monitoring in on top of (or instead of) the RouterOS
API path: install an SNMP client (`pysnmp`, or shell out to
`snmpget`/`snmpwalk`), and on a schedule (cron/Celery beat) poll each
`Device.ip_address` for standard OIDs (`ifInOctets`/`ifOutOctets` for
bandwidth, `sysUpTime`, `hrProcessorLoad`) and write into
`MonitoringReading` — the model/API/UI already expect exactly that shape.

## 6. Full feature history (chronological)

### 2026-08-13 — initial build

1. **Core platform**: Customer CRM; Billing (tariffs/services/invoices/
   payments); Network management (devices/IP pools, simulated monitoring);
   Customer self-service portal; Support ticketing.
2. **Scheduling module** — staff-only month calendar for field jobs
   (installation/repair/maintenance/site-visit/office-task, optionally
   linked to a customer and/or ticket) and staff shifts (roster blocks).
   New `/api/staff-users/` endpoint for assignable staff.
3. **Stock / Inventory module** — Suppliers, Products (serial+MAC tracked,
   for CPE, or pure quantity-tracked for bulk consumables), Stock Receipts
   (against a real supplier invoice, optional attachment), Stock Issues
   (to a technician, optionally linked to a Scheduling job). Quantity-
   tracked products keep an auditable movement ledger, not a mutable
   counter; manual "Adjust" action for damage/loss/recount. Low-stock
   threshold + badge. `/media/` uploads on a dedicated Docker volume so
   they survive rebuilds.
4. **Two-factor authentication (TOTP)** — opt-in per account via "Account
   settings." QR-code enrollment, one confirm code to activate, 10 backup
   codes. New `accounts.TwoFactorAuth`/`TwoFactorBackupCode` models;
   `pyotp` + `qrcode` added as dependencies.
5. **CSV import** for Customers and Tariffs — generic importer
   (`config/csv_import.py`), preview-before-commit, per-row error
   reporting, column aliasing so a raw Splynx export (tab-delimited, its
   own column names, "R"-prefixed currency) uploads with no manual
   reformatting. Cross-file duplicate detection via Splynx's Portal login,
   stored in each imported customer's Notes field.
6. **Data migration from Splynx**: 1,592 real customers imported this way
   (full detail in section 8). Splynx's "Status" (Online/Offline) is
   network connectivity, not account status, so deliberately not mapped —
   all imported customers landed on account status "New".
7. **Sortable columns** across Customers/Tariffs/Services/Invoices/
   Payments, plus real server-side pagination (50/page) on Customers and a
   fix for a bug where search only checked the first 100 loaded rows.
   Default Customers sort set to `full_name` ascending.
8. **Invoice date filters** — "0-30/0-60/0-90 days overdue" preset tabs
   (cumulative ranges, unpaid + past due date) plus a custom creation-date
   range filter (`billing/filters.py`'s `InvoiceFilter`).
9. **Automated backups** (`deploy/backup.sh`) — hourly Postgres dumps,
   rotating 48 hourly + 30 daily locally, with an off-site Google Cloud
   Storage path wired in but not yet activated (see section 10).
10. **Production Docker Compose deployment** to the Xneelo VPS.

### 2026-08-14

11. **Quote → Pro Forma → Invoice workflow** — create a Quote from
    Invoices ("+ New quote") instead of a regular invoice; explicit
    one-directional **Convert to pro forma** / **Convert to invoice**
    buttons on the invoice detail page (a Quote can also convert straight
    to an Invoice). Each stage has its own numbering —
    `QUO-000001` → `PF-000001` → `INV-000248` — and the real invoice
    sequence is untouched by quotes/pro formas that never convert. New
    "Quote" / "Pro forma invoice" email templates, each attaching a PDF
    correctly labeled "Quotation" or "Pro Forma Invoice — not a tax
    invoice." **Real bug found and fixed along the way** (not
    quote-specific): creating any invoice/quote with line items was
    failing validation (`items.invoice: This field is required`) because
    of a leftover required field in the item serializer.
11. **Payroll module** (new `payroll` app) — `StaffProfile` (ID number,
    license number, annual leave balance), `AttendanceRecord`,
    `PayrollRun`/`PayrollRunLine`, `LeaveRequest`.
12. **Fleet module** (new `fleet` app) — `Vehicle` (with fuel type),
    `OdometerReading`, `ServiceRecord`, `FuelLog`.
13. **Credit Request workflow** (`billing.CreditRequest`) — Accounts (or
    an admin) can request a credit to a customer's balance for a stated
    reason (billing error, goodwill, outage compensation); only Management
    or Admin can approve (which actually applies the credit) or reject.
    This request/decide shape was later mirrored by the deletion-request
    workflows (item 20).
14. **Section-based staff permissions** (`accounts.User.allowed_sections`,
    `accounts.permissions.section_permission`) — granular per-staff access
    to specific parts of the platform (e.g. Finance), reused by later
    features (bank feeds, billing config).
15. **HTTPS** — host-level Nginx + Certbot in front of the Docker stack
    (`frontend`'s Compose port bound to `127.0.0.1:8080` instead of public
    `80`), live at `https://portal.skybre.co.za`. Two real bugs fixed to
    get login actually working end-to-end over it: a missing `https://`
    scheme on `CORS_ALLOWED_ORIGINS` was crash-looping the backend
    container, and the frontend's internal nginx was overwriting
    `X-Forwarded-Proto` with its own hop's scheme instead of passing the
    outer nginx's through, causing an SSL-redirect loop on every `/api/`
    request.
16. **Firewall + media security** — `ufw` enabled (22/80/443 only,
    default-deny otherwise); unauthenticated `/media/...` access closed off
    with signed, 5-minute-expiry links generated at API-response time
    (`config/media_security.py`).
17. **Backup restore tested end-to-end** — latest hourly backup restored
    into a throwaway database, row counts verified to match production
    exactly, throwaway DB dropped.

### 2026-08-15

18. **`EmailSettings` singleton** (Configs → Email Settings) — SMTP
    configuration editable from the UI (host/port/credentials/etc.)
    instead of requiring an `.env` edit + restart. Falls back to the
    `.env`-driven setting for any field left blank, so a fresh install
    with this table empty behaves exactly as before this model existed.

### 2026-08-16 — RADIUS/Mikrotik integration, reseller layer, deletion governance

_(This is the batch this assistant has the least first-hand session detail
on — described from the current codebase and the dedicated setup doc that
was written for it, not from a witnessed conversation.)_

19. **RADIUS / OVPN + Mikrotik integration**, built and tested end-to-end
    against a real FreeRADIUS install on the VPS (`radtest`/`radclient`
    simulation of a real login + accounting session) for authenticating
    OVPN clients on a Mikrotik at **Teraco Johannesburg**:
    - `billing.Service` gained a RADIUS username/password, a
      `radius_connection_type` (OVPN vs PPPoE, defaults to OVPN so existing
      services are unaffected), an `ip_assignment_mode` for PPPoE services
      (auto/manual/pool), and an optional `connection_rule` (a per-service
      speed override tied to a specific device — see `network.ConnectionRule`).
    - `network.IPPool` gained a `category`: **Customer IP Pool** (normal
      assignment), **Net IP Pool** (addresses FreeRADIUS hands to
      authenticated OVPN clients), and **Walled Garden** (no real internet
      route — suspended PPPoE customers are automatically moved here
      instead of their normal address while suspended, so pointing the
      router's firewall at this subnet shows a captive "please pay" page
      or just drops traffic).
    - New `radiusauth` app: `RadCheck`/`RadReply` mirror FreeRADIUS's own
      stock `radcheck`/`radreply` schema exactly (so FreeRADIUS's default,
      unmodified `queries.conf` just works), auto-synced from
      `Service`/RADIUS-credential changes via signals — nobody edits these
      tables by hand. `RadAcct` mirrors FreeRADIUS's stock `radacct`
      accounting table, read-only, powering a live-sessions/session-
      history view under Networking. `RadiusNasClient` is the
      staff-managed registry of Mikrotik/NAS devices allowed to talk to
      FreeRADIUS (name, IP, shortname, shared secret, optional `realm` tag
      for reporting only), with a "push to router" action and a
      `render_clients_conf` management command that turns these rows into
      a `clients.conf` snippet for staff to install on the real FreeRADIUS
      server. `OvpnSettings` is a singleton (Configs → OVPN, later moved —
      see below) holding the default FreeRADIUS server address.
    - A successful RADIUS login returns `Framed-IP-Address` (from the Net
      IP Pool) and `Mikrotik-Rate-Limit` (from the service's tariff
      speed), which RouterOS applies automatically with no extra queue
      config needed.
    - **The Mikrotik hardware side itself was not tested against real
      equipment** — a fully-commented, placeholder-filled RouterOS script
      was written for the Teraco JHB device, meant to be reviewed and
      tested on a maintenance window, not applied blind.
    - Separately, `network.Device` gained a real RouterOS **live API**
      integration (`api_enabled`, `api_username`, `api_password` —
      recoverable, not hashed, since it must be resent on every call, same
      reasoning as `Service.radius_password` — `api_use_ssl`,
      `api_wan_interface` for bandwidth polling) powering test-connection,
      poll-now, live-sessions, and disconnect-session actions, plus router
      feature toggles: `block_disabled_customers`, `enable_mpsk`
      (multi-PSK Wi-Fi), `enable_shaper` + `shaping_type`,
      `enable_wireless_access_list`, `wireless_interface`. Backend logic in
      `network/mikrotik.py`.
    - **OVPN settings moved from Configs to Networking** (alongside RADIUS
      Clients), at Rudolph's explicit request ("Should we move OVPN to
      networking?" → "Yes please"). This briefly regressed after one
      deploy due to a stale cached Docker build layer, then was
      re-confirmed fixed.
20. **Partner / reseller layer** (`customers.Partner`) — a reseller who
    sells Skybre's services under their own customer base; customers can
    be tagged to a `Partner` for reporting/commission purposes
    (`commission_rate`). Which staff can see which partners' customers/
    devices is a separate per-staff restriction
    (`accounts.User.allowed_partners`/`visible_partners`,
    `network.Device.visible_partners`).
21. **Customer deletion-request governance** (`customers.CustomerDeletionRequest`)
    — deleting a Customer cascades away *everything* (services, RADIUS
    logins, assigned IPs, invoices, payments, credit requests, tickets,
    email logs), so it's no longer a plain staff action: any staff with
    Customers access can request it, only Management/Admin can approve
    (which actually deletes) or reject. Mirrors `CreditRequest`'s shape.

### 2026-08-17 — deletion governance extended, recurring billing

22. **Quote / Pro forma deletion-request governance**
    (`billing.InvoiceDeletionRequest`) — direct Rudolph request: *"please
    add a delete button to quotes and pro forma invoices but approved by
    management."* "Request + approval workflow" was chosen as the design.
    Deliberately scoped to **quote/pro-forma-status invoices only** — real
    invoices (draft/unpaid/paid/overdue/cancelled) are untouched and staff
    with Finance access can still delete those directly, same as before.
    Mirrors `CustomerDeletionRequest`'s shape; both models snapshot the
    deleted record's display name/number onto the request row (`SET_NULL`
    FK) so the audit trail survives after the real row is gone.
23. **Recurring billing automation.** Rudolph asked what the actual
    automation workflow was for recurring invoicing → statements →
    reminders → auto-suspension, and it turned out none of it was
    automated — invoices and reminders were all being sent by hand.
    Design was based on Splynx reference screenshots Rudolph shared
    (payment methods → days-to-suspension, reminder schedules, per-
    customer billing config, month-start invoice generation preview), then
    4 design decisions were confirmed:
    - **Mechanism:** host crontab + a Django management command, not
      Celery (`billing/management/commands/run_recurring_billing.py`) —
      the crontab line is documented in the command's own help text but
      **has not been installed on the server yet** (see `OUTSTANDING.md`).
    - **Opt-in:** per customer, off by default
      (`CustomerBillingConfig`, one row per customer, lazily created via
      `for_customer()`).
    - **Run mode, for now:** manual Preview → Run from Finance →
      "Recurring Billing," not a fully automatic nightly run.
    - **Auto-suspension:** left disabled for now, even though
      `PaymentMethod` supports a configured days-to-suspension per method
      (mirroring the Splynx reference) — the blocking logic exists in
      `recurring.py`'s `_process_blocking` but nothing triggers it
      automatically day-to-day yet.
    - Engine: `billing/recurring.py`'s `run_recurring_billing(run_date,
      partner_ids=None, commit=False, triggered_by=None)` — generates the
      next invoice for opted-in active services whose billing date has
      arrived, sends reminders per `ReminderSettings` (with a same-real-day
      de-dupe check), evaluates blocking/suspension rules (present, not
      yet auto-triggered). Accepts an optional `partner_ids` filter for a
      partner-specific billing run (ties into item 20's reseller layer).
    - New "Payment received" email template, auto-sent when a `Payment` is
      recorded for a customer with billing notifications enabled.
    - New Configs → Billing tab (Payment Methods / Billing Defaults /
      Reminders) and a new Billing tab on each customer's detail page
      (opt-in toggle, billing address override, admin "reset to
      defaults").
    - **Real bug found and fixed:** the reminder de-duplication check
      originally compared against the *simulated* `run_date` parameter
      instead of the real wall-clock day (`timezone.localdate()`), which
      could have double-sent a reminder on a backdated re-run within the
      same real day. Fixed before shipping.
    - Delivered as `skybre-recurring-billing.zip`; 66 dedicated checks plus
      the full regression suite passed; deployed and **confirmed working**
      by Rudolph ("Deplyed sucessfully").

### 2026-08-17 / 08-18 — bank feed integration

24. **Bank feed integration.** Rudolph's request: *"there needs to be a
    option to add 4 different FNB accounts that reads the FNB bank API and
    bring into the platform payments received from customers."* This
    session's web access was completely blocked (an org egress policy
    denial, not transient), so FNB's real API details (base URL, auth
    flow, response shape) could not be looked up or verified — disclosed
    to Rudolph directly rather than guessing, and the feature was built so
    only one file needs to change once real FNB docs/credentials exist.
    Four design decisions were confirmed: build generically for now (no
    real FNB access yet); match transactions to customers via the existing
    `Customer.customer_id` reference (format `CUS-XXXXXX`, already shown
    to customers on their statement PDF); staff must review and confirm
    every match before it posts as a real payment (no auto-posting); poll
    hourly once a real API exists.

    New `bankfeeds` app:
    - `BankAccount` — any number of bank accounts (covers the "4 different
      FNB accounts" ask with no hardcoded limit), with write-only API
      credentials (same pattern as SMTP/RADIUS secrets elsewhere).
    - `BankTransaction` — lifecycle `unmatched` → `matched` (reference-
      matched or manually assigned) → `confirmed` (creates a real
      `billing.Payment` — the *only* action that ever touches customer
      balance) or `ignored`. Debit transactions (`amount <= 0`) auto-ignore
      at ingest and can never become a payment. A confirmed transaction can
      never be unmatched/ignored/re-confirmed.
    - `BankFeedSyncLog` — one row per sync attempt (API or manual "sync
      now"), feeding a History tab.
    - **Matching** (`matching.py`): regex `CUS[\s\-]?(\d{6})` against the
      transaction description; deliberately returns no match (falls to
      manual review) if zero or more than one *distinct* reference is
      found — never guesses.
    - **FNB API client** (`fnb_client.py`) — explicitly an unverified
      placeholder OAuth2-client-credentials-shaped implementation,
      isolated in its own file so only it needs to change once real FNB
      access exists; every other module only depends on the normalized
      dict shape it returns.
    - **CSV import** (`csv_import.py`) — a fully working bridge available
      *today*: parses a generic bank statement CSV, deterministic
      `external_id` hash so re-uploading the same file is a safe no-op,
      exposed via `import-preview`/`import-commit` actions.
    - Hourly sync management command exists
      (`sync_bank_feeds`) but, like recurring billing's cron, **is not yet
      installed on the server** (see `OUTSTANDING.md`) — there's nothing
      real to poll yet without FNB credentials.
    - New Finance → "Bank Feeds" tab (Accounts / Review / History
      sub-tabs).
    - Delivered as `skybre-bank-feeds.zip`; 58 dedicated checks plus the
      full regression suite (351 checks total, zero regressions) passed.
      **Not yet confirmed deployed by Rudolph** as of this writing.

### 2026-08-18

25. **Documentation consolidation** — this file rewritten as the single
    "what is this project" entry point, folding in `README.md`,
    `DEPLOYMENT.md`, `BACKUP.md`, `RADIUS_SETUP.md`,
    `SECURITY_AND_REDUNDANCY.md`, `BANK_FEEDS.md`, and
    `DEPLOY_quote_proforma_invoicing.md`, which have all been removed from
    the repo. `OUTSTANDING.md` created as the one other permitted `.md`
    file, holding just the to-do list.

## 7. Security checklist — status

- [x] `DEBUG=False`
- [x] Real `SECRET_KEY` generated (not the example value)
- [x] `ALLOWED_HOSTS` set to the real domain (`portal.skybre.co.za`)
- [x] `CORS_ALLOW_ALL_ORIGINS=False`, `CORS_ALLOWED_ORIGINS=https://portal.skybre.co.za`
- [x] Strong, non-default DB password
- [x] **HTTPS — live since 2026-08-14** (see item 15 above)
- [x] No demo data ever seeded against this deployment
- [x] Automated hourly backups (item 9), restore tested end-to-end (item 17)
- [x] `ufw` firewall — 22/80/443 only, default-deny (item 16)
- [x] `/media/...` access requires a signed, short-lived link (item 16)
- [x] Two-factor authentication (TOTP) available platform-wide (item 4) — **not yet turned on for Rudolph's own admin account**
- [ ] Password minimum length is currently only 6 characters
      (`AUTH_PASSWORD_VALIDATORS` → `MinimumLengthValidator`); worth raising
      to 10+ for a system holding real PII/billing data — a one-line
      settings change, tracked in `OUTSTANDING.md`.
- [ ] Off-site (GCS) backup copies — see section 10, Phase 2, still pending.

## 8. Data migration from Splynx (customers)

Skybre's real customer base previously lived in Splynx. 1,592 customers
were imported from a Splynx export ("Skybre Customers list 7.csv") via the
CSV importer in item 5. Two things worth knowing if more Splynx export
files show up later:

- That file was described as a **partial/filtered export** — there may be
  other "list N" files still to import. The importer's duplicate detection
  (via Splynx Portal login, stored in Notes) means re-importing overlapping
  files is safe.
- Only core customer fields came across (name, address, city, balance).
  Splynx's per-customer plan, portal login, and internal ID are preserved
  as text in each customer's **Notes** field, not as real linked records —
  no Tariff/Service records were auto-created. Turning those into real
  Service subscriptions is still a manual/future step.

## 9. RADIUS / OVPN setup (FreeRADIUS + Mikrotik at Teraco JHB)

This is how the platform's own Service/Tariff/IP Pool data becomes the
source of truth for a real FreeRADIUS server authenticating OVPN clients on
the Mikrotik at Teraco Johannesburg (see feature history item 19 for the
what/why; this section is the concrete how-to).

**How it fits together:** staff set a RADIUS username/password on a
Service (Services → Edit → RADIUS/OVPN login) — unrelated to the
customer's own portal login. A "Net IP Pool" (Networking → IP Pools) holds
the addresses FreeRADIUS hands to authenticated OVPN clients, separate from
the ordinary "Customer IP Pool." The `radiusauth` app keeps FreeRADIUS's
own `radcheck`/`radreply` tables in sync automatically via signals — nobody
edits those tables by hand — and FreeRADIUS writes accounting data to
`radacct`, which the platform reads read-only for the Networking → Live
Sessions view. A successful login gets back a `Framed-IP-Address` and a
`Mikrotik-Rate-Limit`; RouterOS applies both automatically.

1. **Install FreeRADIUS on the VPS:**
   ```bash
   sudo apt-get update
   sudo apt-get install -y freeradius freeradius-postgresql freeradius-utils
   sudo systemctl stop freeradius   # stop the default sqlite instance while reconfiguring
   ```
2. **Point the SQL module at this platform's Postgres**, in
   `/etc/freeradius/3.0/mods-available/sql`:
   ```
   dialect = "postgresql"
   driver = "rlm_sql_postgresql"
   radius_db = "host=localhost port=5432 dbname=ispplatform user=postgres password=<DB password>"
   read_groups = no
   read_profiles = no
   ```
   (group tables are unused — every check/reply item is per-username,
   synced straight from a Service.) Enable it:
   ```bash
   sudo ln -sf ../mods-available/sql /etc/freeradius/3.0/mods-enabled/sql
   ```
   (`sql` is already wired into `authorize`/`accounting`/`post-auth` in
   `sites-enabled/default` by default — no changes needed there.)
3. **Create the FreeRADIUS tables Django doesn't manage** (group/post-auth
   logging tables FreeRADIUS's default queries touch but this platform
   doesn't use):
   ```bash
   sudo -u postgres psql -d ispplatform < /etc/freeradius/3.0/mods-config/sql/main/postgresql/schema.sql
   ```
   Safe to run even though `radcheck`/`radreply`/`radacct` already exist —
   the stock file uses `CREATE TABLE IF NOT EXISTS`. (You may see one
   harmless error about a missing `class` column index on `radacct` —
   this platform doesn't use the RADIUS Class attribute, so that index
   isn't needed.)
4. **Add the Mikrotik as a RADIUS client** in two places, with the *same*
   shared secret in both: this platform (Networking → RADIUS Clients →
   "+ New RADIUS client"), and FreeRADIUS's own `clients.conf`:
   ```bash
   cd backend
   python manage.py render_clients_conf --output /etc/freeradius/3.0/clients.conf.d/skybre_clients.conf
   ```
   then `sudo systemctl reload freeradius`. Re-run + reload any time a
   RADIUS client is added/edited — FreeRADIUS only reads `clients.conf` on
   start/reload, not live from SQL.
5. **Start and test:**
   ```bash
   sudo systemctl start freeradius
   # or: freeradius -X   (verbose, after stopping the service)
   radtest <radius_username> <radius_password> localhost 0 <freeradius-client-secret>
   ```
   A working setup returns `Access-Accept` with `Framed-IP-Address` and
   `Mikrotik-Rate-Limit` matching the Service's assigned Net IP and tariff
   speed. A suspended/terminated Service or wrong password correctly
   returns `Access-Reject`.
6. **The Mikrotik side itself** — `deploy/radius/mikrotik_teraco_jhb.rsc`,
   a fully-commented RouterOS script (RADIUS client config, PPP profile
   with `use-radius=yes`, the OVPN server) with placeholders and a
   verification checklist. **Not tested against real hardware** — review
   carefully and test in a maintenance window.

**Troubleshooting:**
`Access-Reject` for a Service that should work → check its status is
Active and both RADIUS username/password are set (Suspended/Terminated/
Pending are deliberately rejected, see `radiusauth/signals.py`).
`Access-Accept` but no `Framed-IP-Address` → the Net IP Pool is out of
free addresses. FreeRADIUS "connection refused" → check `radius_db`
credentials match `backend/.env`. NAS gets "no response" → the
`clients.conf` secret must exactly match the Mikrotik's own config, and
`clients.conf` must be reloaded after any change. Live Sessions page empty
→ confirm FreeRADIUS accounting is actually enabled and reaching this
database.

## 10. Backup strategy

`deploy/backup.sh` takes an hourly Postgres backup and keeps a rolling
local history: the last **48 hourly** backups (2 days) plus one **daily**
snapshot per day for **30 days**. It can also copy every backup off-site to
Google Cloud Storage once that's configured (Phase 2 below).

**Phase 1 — local hourly backups (live since 2026-08-13):**
```bash
chmod +x deploy/backup.sh
mkdir -p ~/backups
./deploy/backup.sh                     # run once by hand first
tail -5 ~/backups/backup.log
ls -lh ~/backups/hourly ~/backups/daily
crontab -l 2>/dev/null > /tmp/current-cron || true
echo "0 * * * * /home/ubuntu/isp-platform/deploy/backup.sh" >> /tmp/current-cron
crontab /tmp/current-cron
```

**Phase 2 — off-site copies to Google Cloud Storage (still pending, see
`OUTSTANDING.md`):**
1. Create a GCP project + Cloud Storage bucket (region `africa-south1`/
   Johannesburg if offered — lowest latency + data stays in-country for a
   South African business; Storage class **Standard** is fine at this data
   volume). 2. Create a service account with **Storage Object Admin**
   scoped to just that bucket, download its JSON key. 3. Set bucket
   lifecycle rules: delete `hourly/` after 3 days, `daily/` after 35 days.
4. Upload the key to the VPS:
   ```powershell
   scp -o IdentitiesOnly=yes -i "C:\Users\Intel i7\OneDrive\Desktop\Xneelo\Skybre ISP.pem" "G:\Projects\ISP Management\<downloaded-key>.json" ubuntu@154.65.111.61:~/backups/gcs-service-account.json
   ```
5. Install the `gcloud` CLI on the VPS
   (`curl -sSL https://sdk.cloud.google.com | bash`), then set
   `GCS_BUCKET="skybre-isp-backups-<name>"` in `deploy/backup.sh` and
   re-run it by hand once to confirm `OK: uploaded to gs://...` appears in
   the log. From then on it rides the same hourly cron job.

**Restoring:**
```bash
# from a local backup
gunzip -c ~/backups/hourly/skybre_<timestamp>.sql.gz | docker compose exec -T db psql -U <DB_USER> <DB_NAME>

# from an off-site (GCS) backup
gcloud storage cp gs://<bucket>/daily/skybre_<date>.sql.gz ~/restore.sql.gz
gunzip -c ~/restore.sql.gz | docker compose exec -T db psql -U <DB_USER> <DB_NAME>
```
This restores on top of whatever's currently in the database — for a full
clean restore (disaster recovery onto a brand-new VPS), drop/recreate the
database first. **Always test a restore occasionally** into a throwaway
database — an untested backup is a guess, not a safety net (this was done
once, 2026-08-14, and passed).

## 11. Security & redundancy assessment (summary)

A full assessment was done on 2026-08-13 and most of its findings are now
resolved (see the checklist in section 7 and the feature history above for
what changed and when). What's left:

- **Redundancy is currently a single VPS with no failover.** If Xneelo's
  host has a hardware fault, the platform is down until someone rebuilds
  it. Whether that's tolerable depends on whether this platform is in the
  live customer-connectivity path — as of the last check, RADIUS/network
  auth (item 19) is architecturally kept separate from this VPS on
  purpose specifically so that isn't the case, but confirm this is still
  true whenever infrastructure changes.
- **Recommended order of operations, still open:**
  1. Finish GCS off-site backups (section 10, Phase 2) — highest-value
     item left; current backups all live on the same disk as everything
     else.
  2. Turn on Xneelo's own VPS-level snapshots, if offered — an independent
     safety net against disk corruption/`rm -rf`/botched updates that
     app-level Postgres dumps don't cover.
  3. Basic uptime monitoring (e.g. UptimeRobot) — right now an outage is
     only discovered when a staff member complains.
  4. Write down (and once, rehearse) a disaster-recovery runbook: provision
     a new VPS → clone the GitHub repo → refill `.env` from
     `.env.production.example` → restore the latest off-site backup →
     `docker compose up -d --build` → repoint DNS. This turns "the VPS is
     gone" into a known 30–60 minute procedure instead of a panic.
  5. A full high-availability setup (load-balanced app servers + DB
     replication) is **not** justified yet — revisit only if this platform
     ever becomes directly load-bearing for live customer connectivity,
     or an uptime SLA to customers exists.
- **Password minimum length** — still 6 characters; worth raising to 10+.

## 12. Deploy workflow

### Initial VPS setup (already done; kept here for disaster-recovery / a second environment)

Docker Compose path (the one actually used):
```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER   # log out and back in
# get the code onto the VPS, then:
cp backend/.env.production.example backend/.env
nano backend/.env   # SECRET_KEY, ALLOWED_HOSTS, DB_PASSWORD/POSTGRES_PASSWORD, CORS_ALLOWED_ORIGINS, SEED_DEMO_DATA=False
docker compose build
docker compose up -d
docker compose logs -f backend   # watch migrations, then Ctrl-C
docker compose exec backend python manage.py createsuperuser
```
Then HTTPS via a host-level Nginx + Certbot in front of the Compose
`frontend` container (bind its port to `127.0.0.1:8080` instead of public
`80`, proxy from host Nginx, `sudo certbot --nginx -d yourdomain.com`), and
set `SECURE_SSL_REDIRECT`/`SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE`/
`BEHIND_HTTPS_PROXY=True` in `.env` once it's live. (A bare-metal,
non-Docker path using systemd + host Nginx also exists and was documented,
but Docker Compose is what's actually deployed.)

### Ongoing feature deploys (the actual mechanism used for every item in section 6)

1. **Package.** Changed files only, flat top-level `backend/...` /
   `frontend/...` structure (never wrapped in an extra
   `<feature>-package/` folder), zipped as `skybre-<feature>.zip`. Always
   verify with `unzip -l` before handing it over.
2. **Deliver.** Sent to Rudolph in-chat; his browser saves every
   downloaded file to **`G:\Projects\ISP Management\`** — the current
   standing download location (changed 2026-08-18; it was previously
   `C:\Users\Intel i7\OneDrive\Desktop\Xneelo\`).
   **The SSH key did *not* move** — `Skybre ISP.pem` is still at
   `C:\Users\Intel i7\OneDrive\Desktop\Xneelo\Skybre ISP.pem`. The `scp`
   command below therefore reads its key from one folder and its payload
   from another; that's correct, not a typo.
3. **Upload to the VPS** (from Rudolph's machine, PowerShell):
   ```powershell
   scp -o IdentitiesOnly=yes -i "C:\Users\Intel i7\OneDrive\Desktop\Xneelo\Skybre ISP.pem" "G:\Projects\ISP Management\skybre-<feature>.zip" ubuntu@154.65.111.61:/home/ubuntu/
   ```
4. **Unpack, migrate, rebuild, restart** (SSH session on the server):
   ```bash
   cd /home/ubuntu/isp-platform
   unzip -o ~/skybre-<feature>.zip
   docker compose exec backend python manage.py migrate
   docker compose build backend frontend
   docker compose up -d
   ```
   (A doc-only `.md` update needs only the `unzip -o` step.)
5. **Verify** — `docker compose ps` (all `Up`, no `SystemCheckError`),
   `docker compose logs backend --tail 30` / `frontend --tail 30`, then a
   quick pass through the new feature in the browser.
6. **Back up to GitHub, once verified** (occasional, not every deploy;
   this is *not* how the live server gets updated — it never runs
   `git pull`):
   ```bash
   git add <changed paths>
   git commit -m "<what changed>"
   git push
   ```

**Known deploy pitfalls:** a placeholder local file path was once
correctly rejected by Rudolph ("supply me the correct path"); a Docker
Compose rebuild can occasionally serve a **cached layer** of an old build
even after `docker compose build` — if a deployed change doesn't seem to
have taken effect, check for that before assuming the code is wrong.

## 13. Where the code lives

- **Source of truth / live deployment:** `/home/ubuntu/isp-platform` on the VPS (154.65.111.61)
- **Rudolph's local working/project folder (Windows):** `G:\Projects\ISP Management\` — where delivered `.zip` packages are downloaded to and kept before being `scp`'d up (moved here 2026-08-18 from `C:\Users\Intel i7\OneDrive\Desktop\Xneelo\`)
- **SSH key:** `C:\Users\Intel i7\OneDrive\Desktop\Xneelo\Skybre ISP.pem` — deliberately still in the old Xneelo folder, it was *not* moved with the project files
- **Backup / history:** GitHub repo `Rudolph-1979/skybre-isp-platform` (private) — pushed to occasionally, not part of the deploy path (section 12)
- Secrets (`backend/.env`) are **not** in this repo (gitignored) — they
  exist only on the VPS. To rebuild from scratch, copy
  `backend/.env.production.example` and refill real values.
- **Database backups:** `~/backups/` on the VPS (section 10), plus Google
  Cloud Storage once Phase 2 is set up.
- **The other `.md` file in this repo:** `OUTSTANDING.md` — the current
  to-do list. No other `.md` files should exist per the two-file
  convention at the top of this document.
