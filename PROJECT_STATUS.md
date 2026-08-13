# Skybre ISP Platform — Project Status

_Last updated: 2026-08-13_

## What this is

A Splynx-inspired ISP management platform (customer CRM, billing, network
device/IP management, customer self-service portal, support ticketing) built
for **Skybre Pty Ltd**'s real business use — not a demo. See `README.md` for
architecture/stack and `DEPLOYMENT.md` for how it's deployed.

## Current state: LIVE, in production, empty of data

- Deployed via Docker Compose on a Xneelo Cloud VPS.
- **VPS IP:** `154.65.111.61` (plain HTTP for now, no domain/TLS yet)
- **Access:** SSH as `ubuntu@154.65.111.61` using the `Skybre ISP` key pair
  (private key file: `Skybre ISP.pem`, kept locally by Rudolph — not in this repo)
- **Project path on VPS:** `~/isp-platform` (i.e. `/home/ubuntu/isp-platform`)
- Stack is up: `db` (Postgres 16), `backend` (Django/Gunicorn), `frontend` (Nginx serving React build + proxying `/api`)
- `SEED_DEMO_DATA=False` — **no fictional demo data was ever loaded.** All
  customers/tariffs/invoices you see are real Skybre data, entered manually.
- Admin login: username `Rudolph`, real superuser, `role=admin` (had to be
  manually fixed once — Django's `createsuperuser` doesn't set our custom
  `role` field, so it defaulted to `customer`; if you ever create another
  superuser, immediately run:
- Code is backed up to GitHub: `github.com/Rudolph-1979/skybre-isp-platform` (private repo)

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
- [ ] No database backup taken yet — do this soon:
      `docker compose exec db pg_dump -U ispplatform ispplatform > backup-$(date +%F).sql`
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
   auth/accounting. The platform's network monitoring is currently
   **simulated**, not real SNMP/RADIUS polling. Wiring in real data is a
   planned separate phase — see README.md "What's simulated vs. real".
3. Remove the demo-logins hint box from the login page.
4. Take and test a database backup/restore.

## Where the code lives

- **Source of truth / live deployment:** `~/isp-platform` on the VPS (154.65.111.61)
- **Backup / history:** GitHub repo `Rudolph-1979/skybre-isp-platform` (private)
- Secrets (`backend/.env`) are **not** in this repo (gitignored) — they exist
  only on the VPS. If you ever need to rebuild from scratch, copy
  `backend/.env.production.example` and refill the real values.
