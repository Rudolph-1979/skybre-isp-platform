# Skybre ISP Platform — Project Status

_Last updated: 2026-08-13_

## What this is

A Splynx-inspired ISP management platform (customer CRM, billing, network
device/IP management, customer self-service portal, support ticketing,
scheduling) built for **Skybre Pty Ltd**'s real business use — not a demo.
See `README.md` for architecture/stack and `DEPLOYMENT.md` for how it's
deployed.

## Current state: LIVE, in production, real customer data loaded

- Deployed via Docker Compose on a Xneelo Cloud VPS.
- **VPS IP:** `154.65.111.61` (plain HTTP for now, no domain/TLS yet)
- **Access:** SSH as `ubuntu@154.65.111.61` using the `Skybre ISP` key pair
  (private key file: `Skybre ISP.pem`, kept locally by Rudolph — not in this repo)
- **Project path on VPS:** `~/isp-platform` (i.e. `/home/ubuntu/isp-platform`)
- Stack is up: `db` (Postgres 16), `backend` (Django/Gunicorn), `frontend` (Nginx serving React build + proxying `/api`)
- **1,592 real customers imported from Splynx** (see "Data migration" below).
  `SEED_DEMO_DATA=False` — no fictional demo data was ever loaded.
- Admin login: username `Rudolph`, real superuser, `role=admin` (had to be
  manually fixed once — Django's `createsuperuser` doesn't set our custom
  `role` field, so it defaulted to `customer`; if you ever create another
  superuser, immediately run:
  ```bash
  docker compose exec backend python manage.py shell -c \
    "from accounts.models import User; u = User.objects.get(username='NEWUSER'); u.role = 'admin'; u.save()"
  ```
- Code is backed up to GitHub: `github.com/Rudolph-1979/skybre-isp-platform` (private repo)

## Feature history (roughly in order built)

1. Core platform: Customer CRM, Billing (tariffs/services/invoices/payments),
   Network management (devices/IP pools, simulated monitoring), Customer
   portal, Support tickets.
2. Production Docker Compose deployment to the Xneelo VPS.
3. **CSV import** for Customers and Tariffs — generic importer
   (`config/csv_import.py`) with preview-before-commit, per-row error
   reporting, and column aliasing so a raw Splynx export (tab-delimited,
   its own column names, "R"-prefixed currency) can be uploaded directly
   with no manual reformatting. Cross-file duplicate detection via
   Splynx's Portal login, stored in each imported customer's Notes field.
4. **Data migration from Splynx**: 1,592 real customers imported this way
   (see below). Splynx's "Status" column (Online/Offline) is network
   connectivity, not account status — deliberately *not* mapped, so all
   imported customers landed on account status "New".
5. **Sortable columns** (click a header to sort asc/desc) added across
   Customers, Tariffs, Services, Invoices, and Payments. Customers page
   also got real server-side pagination (50/page) and fixed a bug where
   search only checked the first 100 loaded rows instead of the whole
   customer base. Default Customers sort is `full_name` ascending (an
   earlier default of "newest created first" looked confusingly like
   Z→A because the Splynx import happened to load customers in
   alphabetical order).
6. **Invoice date filters**: quick preset tabs for "0-30/0-60/0-90 days
   overdue" (unpaid invoices whose due date has passed, within that many
   days — cumulative ranges, not exclusive buckets) plus a custom date
   range filter on invoice creation date. Backend: `billing/filters.py`
   (`InvoiceFilter`).
7. **Scheduling module** (new `scheduling` Django app): a month calendar
   view for field jobs (installation/repair/maintenance/site-visit/
   office-task, each optionally linked to a customer and/or a ticket) and
   staff shifts (roster blocks, not tied to a customer). Staff-only —
   customers never see this. New `/api/staff-users/` endpoint lists
   assignable staff (also usable for ticket assignment, which never had a
   UI selector before this).
8. **Automated backups** (`deploy/backup.sh`, see `BACKUP.md`): hourly
   Postgres dumps via cron, rotating 48 hourly + 30 daily locally.
   Off-site copy to Google Cloud Storage is wired into the same script but
   only activates once `GCS_BUCKET`/`GCS_KEY_FILE` are configured — check
   `BACKUP.md` "Phase 2" for whether that's been done yet (currently
   deferred at Rudolph's request — no rush while data volume is low).
9. **Stock / Inventory module** (new `inventory` Django app): Suppliers,
   Products (either individually tracked by serial number + MAC address —
   for routers/ONTs/CPE — or tracked purely by quantity, for bulk
   consumables like cable/connectors), Stock Receipts (check stock in
   against a real supplier invoice, with an optional attachment upload
   for the invoice photo/PDF), and Stock Issues (issue stock out to a
   technician, optionally linked to a Scheduling job — or standalone).
   Quantity-tracked products keep an auditable movement ledger rather
   than a mutable counter, so on-hand totals can't silently drift; a
   manual "Adjust" action covers damage/loss/recount corrections.
   Low-stock threshold + badge per product. Staff-only, same as
   Scheduling. New `/media/` uploads are stored on a dedicated Docker
   volume (`media_data`) so they survive rebuilds.

## Data migration from Splynx (customers)

Skybre's real customer base previously lived in Splynx. 1,592 customers
were imported from a Splynx export ("Skybre Customers list 7.csv") via the
CSV importer described above. Two things worth knowing if more Splynx
export files show up later:

- That file was described as a **partial/filtered export** (not
  necessarily everyone) — there may be other "list N" files still to
  import. The importer's duplicate detection (via Splynx Portal login,
  stored in Notes) means re-importing overlapping files is safe.
- Only core customer fields came across (name, address, city, balance).
  Splynx's per-customer plan, portal login, and internal ID are preserved
  as text in each customer's **Notes** field, not as real linked records —
  no Tariff/Service records were auto-created from the import. Turning
  those into real Service subscriptions (customer ↔ tariff links) is
  still a manual/future step.

## Security checklist (from DEPLOYMENT.md) — status

- [x] `DEBUG=False`
- [x] Real `SECRET_KEY` generated (not the example value)
- [x] `ALLOWED_HOSTS` set to the VPS IP only
- [x] `CORS_ALLOW_ALL_ORIGINS=False`, `CORS_ALLOWED_ORIGINS` set to the VPS origin
- [x] Strong, non-default DB password
- [ ] **HTTPS not live yet** — `SECURE_SSL_REDIRECT` / `SESSION_COOKIE_SECURE` /
      `CSRF_COOKIE_SECURE` / `BEHIND_HTTPS_PROXY` are all `False` (correct for
      now, since there's no domain/TLS yet — flip to `True` once Certbot is set up)
- [x] No demo data ever seeded against this deploy
- [x] Automated hourly database backups — see `BACKUP.md`. Local rotation is
      live; off-site copy to Google Cloud Storage is Phase 2 in that doc,
      pending bucket/service-account setup on Rudolph's side.
- [ ] Firewall/security group: currently allows 22, 80 (added manually to
      fix a connection-timeout issue), 443 not yet confirmed open

## Known cosmetic issue (not yet fixed)

The login page still shows a static "Demo logins" hint box referencing
accounts that don't exist in this deployment. Harmless but should be removed
before customers see it.

## Explicitly deferred / future work

1. **HTTPS** — need a domain pointed at 154.65.111.61, then run Certbot per
   `DEPLOYMENT.md` Option A step 5.
2. **RADIUS integration** — Skybre runs a real FreeRADIUS system for network
   auth/accounting (see uploaded files like "Sessions - Radius - Skybre Pty
   Ltd.csv"). The platform's network monitoring is currently **simulated**
   (`network.MonitoringReading` rows generated by a demo command, not real
   SNMP/RADIUS polling), and there's no live per-customer online/offline
   status either — both would need this RADIUS integration. Explicitly put
   on hold for now, not being worked on.
3. **GCS off-site backups (Phase 2 of BACKUP.md)** — pending Rudolph
   creating a Google Cloud bucket + service account key.
4. Turn imported customers' Splynx plan info (currently just text in
   Notes) into real Tariff/Service links.
5. Remove the demo-logins hint box from the login page.
6. Test a real restore from a backup (not just confirm backups are being
   created — actually restore one into a throwaway database and verify).
7. Any remaining Splynx "list N" export files, if Rudolph has more to
   import.

## Where the code lives

- **Source of truth / live deployment:** `~/isp-platform` on the VPS (154.65.111.61)
- **Backup / history:** GitHub repo `Rudolph-1979/skybre-isp-platform` (private)
- Secrets (`backend/.env`) are **not** in this repo (gitignored) — they exist
  only on the VPS. If you ever need to rebuild from scratch, copy
  `backend/.env.production.example` and refill the real values.
- **Database backups:** `~/backups/` on the VPS (see `BACKUP.md`), plus
  Google Cloud Storage once Phase 2 there is set up.
