# Security & Redundancy Assessment — Skybre ISP Platform

_Prepared 2026-08-13. Based on a direct review of the actual codebase and
`PROJECT_STATUS.md`'s documented VPS configuration — not a generic checklist._

## Part 1 — Security: what's actually in place

### Solid — confirmed by reading the code

- **Every API endpoint requires authentication.** The Django REST
  Framework default (`DEFAULT_PERMISSION_CLASSES`) is `IsAuthenticated`
  platform-wide, and a scan of every viewset in the codebase confirms
  there is no `AllowAny` anywhere — nothing is accidentally public.
- **Staff-only modules are properly locked down.** Scheduling, Network
  devices/IP pools, Inventory, and staff management all require
  `IsStaffMember` (admin/staff/technician role) — a customer login
  cannot reach them even by guessing the URL.
- **Customer data is scoped per-customer, not just per-login.** Billing
  (invoices, payments, services) and Support Tickets use queryset
  filtering (`ScopedByCustomerMixin` / equivalent) so a customer's token
  only ever returns *their own* records — verified this isn't just a UI
  restriction but an actual database-query restriction, so it holds even
  against direct API calls.
- **JWT auth with reasonable token lifetimes** — 8-hour access tokens,
  7-day refresh tokens, with refresh rotation on. No indefinitely-lived
  tokens floating around.
- **Real, non-default credentials in production** — real `SECRET_KEY`,
  strong DB password, `DEBUG=False`, `ALLOWED_HOSTS` restricted to the
  VPS IP, `CORS_ALLOW_ALL_ORIGINS=False` (per `PROJECT_STATUS.md`'s
  checklist — I can't re-verify the live VPS `.env` from here, only the
  local reference copy, so worth a quick `cat backend/.env` on the VPS
  yourself to double check DEBUG is still `False`).
- **No demo/fictional data ever seeded into this deployment** — the real
  customer data set is the only data in the production database.
- **Automated hourly backups** — local rotation (48 hourly + 30 daily)
  has been live and verified since today.

### ~~The one real gap — HTTPS~~ — fixed 2026-08-14

**HTTPS is now live** at `https://portal.skybre.co.za` — Certbot-issued
cert, HTTP redirects to HTTPS, and the `SECURE_SSL_REDIRECT` /
`SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` / `BEHIND_HTTPS_PROXY`
settings are all on. Login credentials, JWT tokens, and customer/billing
data no longer cross the internet in plain text. (Getting login actually
working over it also turned up and fixed two unrelated bugs along the
way: `CORS_ALLOWED_ORIGINS` missing its `https://` scheme was
crash-looping the backend, and the frontend's internal nginx was
overwriting `X-Forwarded-Proto` before it reached Django, causing an
SSL-redirect loop on every API request.)

### Smaller, worth doing

- **Password minimum length is only 6 characters**
  (`AUTH_PASSWORD_VALIDATORS` → `MinimumLengthValidator` `min_length: 6`).
  For a system holding real customer PII and billing/payment data, I'd
  raise this to at least 10 — a one-line settings change.
- ~~Firewall: `ufw` inactive, only Xneelo's external firewall in
  effect~~ — **fixed 2026-08-14.** `ufw` is now enabled, allowing only
  22/80/443 (IPv4 + IPv6) with default-deny on everything else. Verified
  a fresh SSH connection and the live site both still worked immediately
  after enabling.
- ~~Uploaded stock-receipt attachments reachable by anyone with the
  direct URL, without logging in~~ — **fixed.** `/media/...` links are
  now signed with a 5-minute expiry at the moment a staff-authenticated
  API response hands one out (`config/media_security.py`); a bare,
  guessed, tampered, or stale link now gets a 403 instead of the file.
- **The login page's demo-logins hint box** — cosmetic, but shows
  credentials that look real to anyone visiting the page. Already on the
  deferred list; low priority but easy to remove.

### Bottom line on security

For a platform run by one admin for internal business use, this is in
good shape — the access-control fundamentals (auth everywhere, proper
data scoping, no public endpoints) are the hard part to get right, and
they're right. The gap that actually matters is HTTPS; everything else
here is incremental hardening on top of a sound base.

## Part 2 — Redundancy: what happens if the VPS fails

Worth naming honestly: this is currently a **single VPS with no
failover.** If Xneelo's host has a hardware fault, or the VPS is lost
somehow, the platform goes down until someone rebuilds it. The question
is how much that risk is worth paying to reduce, which depends on one
thing I don't know the answer to: **does this platform going down take
customer internet access down with it, or just the admin/customer-portal
tooling?** If RADIUS/network equipment run independently (which
`PROJECT_STATUS.md` suggests — RADIUS integration is explicitly
"deferred, not yet built into this platform"), then an outage here means
staff can't log invoices or check tickets for a while — inconvenient,
not "customers lose internet." That's a very different urgency than if
this platform were in the live connectivity path. Worth confirming which
is true for you before deciding how much to invest below.

### Tier 1 — cheap/immediate, do these regardless

1. **Finish the off-site GCS backup copy (Phase 2, already built and
   waiting on you).** This is the highest-value item left: your current
   hourly backups live on the *same disk* as everything else — if the
   VPS/disk is lost outright, the backups are lost with it. Off-site
   copies are what actually protect against "the VPS is gone," not just
   "the database got corrupted." You paused this a couple of days ago
   because there was no serious data at risk yet — 1,592 real customers
   and now a stock system arguably changes that calculus; happy to walk
   through bucket setup whenever you want.
2. **Turn on Xneelo's own VPS-level snapshots/backups**, if their control
   panel offers them (most VPS providers do, often for a small monthly
   fee). This is a second, independent safety net at the infrastructure
   level — protects against things our app-level Postgres dumps don't
   (e.g. corrupted disk, accidental `rm -rf`, botched OS update) since it
   snapshots the whole server, not just the database.
3. ~~Actually test a restore~~ — **done 2026-08-14.** Restored the
   latest hourly backup into a throwaway database (`restore_test`),
   confirmed customer/invoice/user row counts matched production exactly,
   dropped the throwaway database afterward. The backup process is
   confirmed to actually work, not just to run.
4. **Basic uptime monitoring** — something free like UptimeRobot pinging
   the site every few minutes and texting/emailing you if it goes down.
   Right now you'd only find out the platform is down when a staff
   member complains — that's a bad way to learn about an outage.

### Tier 2 — a real disaster-recovery runbook (recommended next)

Rather than trying to make one VPS "unbreakable," write down (and once,
actually rehearse) the exact steps to stand up a replacement:

1. Provision a new Xneelo VPS.
2. Clone the GitHub repo, copy `backend/.env.production.example`, refill
   real values.
3. Restore the latest off-site (GCS) database backup.
4. `docker compose up -d --build`.
5. Point DNS (once you have a domain) or update wherever the IP is
   referenced to the new server.

Practiced once, this turns "the VPS is gone" from a panic into a
30–60 minute known procedure. This is the realistic, proportionate answer
to "redundancy" for a platform at this scale — not a second live server,
just a fast, rehearsed way back onto one.

### Tier 3 — full high availability (probably not justified yet)

The "real" redundancy answer — a second live app server behind a load
balancer, database replication to a standby, shared/replicated media
storage — removes the single point of failure entirely, at the cost of
real ongoing complexity and money (roughly 2x infrastructure cost,
managed Postgres or replication setup, more moving parts to maintain).
I'd only recommend this once one of these is true: this platform is
directly in the path of live customer connectivity (not just admin
tooling), you have an actual uptime SLA to customers, or an hour of
downtime has a real quantifiable cost to the business. Worth revisiting
if/when the RADIUS integration lands and this platform starts touching
live network auth — that's the point where "the platform is down" starts
meaning "customers can't get online."

## Recommended order of operations

1. ~~HTTPS~~ — done
2. Finish GCS off-site backups (redundancy — protects the data that
   actually matters; still the highest-value item left)
3. ~~Test a restore once~~ — done
4. ~~Enable `ufw`~~ — done
5. ~~Fix unauthenticated `/media/` access~~ — done
6. Basic uptime monitoring
7. Write down the DR runbook above (don't need to build anything new —
   just document + rehearse once)
8. Everything else (password length, demo-login box) — low-urgency
   cleanup, happy to knock out anytime
