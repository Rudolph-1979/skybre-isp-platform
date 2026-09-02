# RADIUS / OVPN setup (FreeRADIUS + Mikrotik at Teraco JHB)

This covers installing and configuring FreeRADIUS on the VPS so it
authenticates OVPN clients connecting to the Mikrotik at Teraco
Johannesburg, using this platform's own Service/Tariff/IP Pool data as the
source of truth. Everything here has been built and fully tested end to
end (FreeRADIUS install, SQL config, `radtest`/`radclient` simulation of a
real Mikrotik login and accounting session). The one part that could not
be tested from here is the Mikrotik device itself — see
`deploy/radius/mikrotik_teraco_jhb.rsc` for that side, with its own
caveats and verification steps.

## How it fits together

- Staff set a **RADIUS username/password** on a customer's Service
  (Services page → Edit → RADIUS / OVPN login section). Nothing else is
  auto-generated — these are unrelated to the customer's portal login.
- A **Net IP Pool** (Networking → IP Pools, category "Net IP Pool") holds
  the addresses FreeRADIUS hands out to authenticated OVPN clients —
  separate from "Customer IP Pool", which keeps working exactly as before
  for Services generally.
- The `radiusauth` Django app keeps FreeRADIUS's own standard
  `radcheck`/`radreply` SQL tables in sync with that Service data
  automatically (see `backend/radiusauth/signals.py`) — nobody edits those
  tables by hand.
- FreeRADIUS reads `radcheck`/`radreply` via its stock, unmodified
  postgresql `queries.conf` (that's why the table/column names match
  FreeRADIUS's own defaults exactly) and writes accounting data
  (session start/interim/stop) to `radacct`, which the platform reads
  read-only for the Networking → Live Sessions view.
- A successful login gets back a `Framed-IP-Address` (from the Net IP
  Pool) and a `Mikrotik-Rate-Limit` (derived from the Service's tariff
  speed) — RouterOS applies both automatically, no extra queue config
  needed on the Mikrotik side.

## 1. Install FreeRADIUS on the VPS

```bash
sudo apt-get update
sudo apt-get install -y freeradius freeradius-postgresql freeradius-utils
sudo systemctl stop freeradius   # stop the default sqlite-backed instance while we reconfigure
```

## 2. Point the SQL module at the platform's Postgres database

Edit `/etc/freeradius/3.0/mods-available/sql`:

```
dialect = "postgresql"
driver = "rlm_sql_postgresql"
...
radius_db = "host=localhost port=5432 dbname=ispplatform user=postgres password=<your DB password>"
...
read_groups = no
read_profiles = no
```

(`read_groups`/`read_profiles` are turned off because this setup doesn't
use FreeRADIUS's group tables — every check/reply item is per-username,
synced straight from a Service.)

Enable the module:

```bash
sudo ln -sf ../mods-available/sql /etc/freeradius/3.0/mods-enabled/sql
```

The `sql` module is already wired into `authorize`, `accounting`, and
`post-auth` in `sites-enabled/default` out of the box (as `-sql`) — no
changes needed there.

## 3. Create the supporting tables FreeRADIUS needs but Django doesn't manage

Django's `radiusauth` app creates and manages `radcheck`, `radreply`, and
`radacct` (via its own migrations, with column names matching FreeRADIUS's
stock schema exactly). FreeRADIUS's default queries also touch a few
tables this platform doesn't use for anything (group-based auth,
post-auth logging) — create those with the stock schema so FreeRADIUS
doesn't error out looking for them:

```bash
sudo -u postgres psql -d ispplatform < /etc/freeradius/3.0/mods-config/sql/main/postgresql/schema.sql
```

This is safe to run even though `radcheck`/`radreply`/`radacct` already
exist — the stock schema file uses `CREATE TABLE IF NOT EXISTS` and simply
skips them. (You may see one harmless error about a missing `class`
column on an index for `radacct` — this platform doesn't use the RADIUS
Class attribute, so that index isn't needed; everything else still gets
created correctly.)

## 4. Add the Mikrotik as a RADIUS client

Two places need this, and they must use the *same* shared secret:

1. **This platform**: Networking → RADIUS Clients → "+ New RADIUS client"
   — name, IP address, shortname, and the shared secret.
2. **FreeRADIUS's `clients.conf`**: run

   ```bash
   cd backend
   python manage.py render_clients_conf --output /etc/freeradius/3.0/clients.conf.d/skybre_clients.conf
   ```

   then `sudo systemctl reload freeradius`. Re-run this (and reload)
   any time a RADIUS client is added or edited in the admin panel —
   FreeRADIUS only reads `clients.conf` on start/reload, not live from SQL.

## 5. Start FreeRADIUS and test

```bash
sudo systemctl start freeradius
# or, for verbose live debugging (stop the service first): freeradius -X
```

With a Service's RADIUS username/password set and status Active, and at
least one address in a Net IP Pool:

```bash
radtest <radius_username> <radius_password> localhost 0 <freeradius-client-secret>
```

A working setup returns `Access-Accept` with `Framed-IP-Address` and
`Mikrotik-Rate-Limit` matching the Service's assigned Net IP and tariff
speed. A suspended/terminated Service, or a wrong password, correctly
returns `Access-Reject`.

## 6. The Mikrotik side

See `deploy/radius/mikrotik_teraco_jhb.rsc` — a reviewable RouterOS script
for the Teraco JHB device (RADIUS client pointing at this server, PPP
profile with `use-radius=yes`, and the OVPN server itself). It has full
inline comments, placeholders to fill in, and a verification/
troubleshooting checklist at the end. This part has **not** been tested
against real hardware — there's no way to reach that device from here —
so please review it carefully and test on a maintenance window before
relying on it for live customers.

## Troubleshooting

- **`Access-Reject` for a Service that should work**: check its status is
  Active (Suspended/Terminated/Pending are deliberately rejected — see
  `radiusauth/signals.py`) and that both `radius_username` and
  `radius_password` are set.
- **`Access-Accept` but no `Framed-IP-Address`**: the Net IP Pool it's
  drawing from is out of free addresses — add more under Networking → IP
  Pools (category "Net IP Pool").
- **FreeRADIUS can't reach Postgres / "connection refused"**: double
  check `radius_db` in `mods-available/sql` matches the platform's actual
  DB host/port/name/credentials (same ones in `backend/.env`).
- **NAS/Mikrotik gets "no response from server"**: the shared secret in
  `clients.conf` (rendered from RadiusNasClient rows) must exactly match
  what's configured on the Mikrotik itself, and `clients.conf` must be
  reloaded after any change (step 4).
- **Live Sessions page in the admin panel is empty**: it reads `radacct`
  directly — check FreeRADIUS's accounting is actually enabled and
  reaching this database (its own log will show accounting queries if
  `radiusd -X` is run).
