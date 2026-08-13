# Deploying to a VPS

Two paths: **Docker Compose** (recommended — one command brings up Postgres,
the Django API, and the built frontend together) or **bare-metal**
(systemd + a venv, for VPS providers/plans where you'd rather not run
Docker). Pick one; you don't need both.

Either way, start with:

- A VPS running Ubuntu 22.04 or 24.04, at least 1 GB RAM.
- A domain name with an **A record pointing at the VPS's public IP**
  (needed for TLS in step 4 below — you can skip this and use the bare IP
  for testing, just without HTTPS).
- Port 22 (SSH), 80, and 443 open in your VPS provider's firewall/security group.

---

## Option A: Docker Compose (recommended)

### 1. Install Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER   # log out and back in after this
```

### 2. Get the code onto the VPS

```bash
# from your machine
scp -r isp-platform your-user@your-vps-ip:/opt/isp-platform
# or, if it's in git: git clone <your-repo-url> /opt/isp-platform
cd /opt/isp-platform
```

### 3. Configure environment

```bash
cp backend/.env.production.example backend/.env
nano backend/.env
```

Fill in, at minimum:

- `SECRET_KEY` — generate one: `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`
- `ALLOWED_HOSTS` — your domain(s), comma-separated, no `https://`
- `DB_PASSWORD` **and** `POSTGRES_PASSWORD` — same strong value in both
- `CORS_ALLOWED_ORIGINS` — `https://yourdomain.com`
- Leave `SEED_DEMO_DATA=False` unless you specifically want the fictional
  demo customers/invoices/tickets loaded (handy for a first look, not for
  a real launch — see "Demo data" below).

### 4. Build and start

```bash
docker compose build
docker compose up -d
docker compose logs -f backend   # watch migrations run, then Ctrl-C
```

By default `docker compose.yml` publishes the frontend container on host
port 80, so `http://your-vps-ip` (or `http://yourdomain.com` once DNS
propagates) should load the login page immediately — no TLS yet.

Create your real admin account (don't rely on demo credentials in production):

```bash
docker compose exec backend python manage.py createsuperuser
```

### 5. Put HTTPS in front of it

The cleanest way to get free, auto-renewing TLS without teaching the
frontend container about certificates is to install Nginx **on the host**
as a thin TLS-terminating proxy in front of the container:

```bash
# stop publishing port 80 directly to the internet — bind it to localhost only
```

Edit `docker-compose.yml`'s `frontend` service and change:

```yaml
    ports:
      - "80:80"
```

to:

```yaml
    ports:
      - "127.0.0.1:8080:80"
```

Then:

```bash
docker compose up -d   # re-create the frontend container with the new port mapping

sudo apt install -y nginx certbot python3-certbot-nginx
```

Create `/etc/nginx/sites-available/isp-platform`:

```nginx
server {
    listen 80;
    server_name yourdomain.com www.yourdomain.com;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/isp-platform /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
```

Certbot rewrites that file to redirect HTTP → HTTPS and sets up auto-renewal.
Once it's done, also set these in `backend/.env` and restart:

```
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
BEHIND_HTTPS_PROXY=True
```

```bash
docker compose up -d backend
```

### Everyday operations (Docker path)

```bash
# view logs
docker compose logs -f backend
docker compose logs -f frontend

# redeploy after pulling new code
git pull
docker compose build
docker compose up -d

# database backup
docker compose exec db pg_dump -U ispplatform ispplatform > backup-$(date +%F).sql

# database restore
cat backup-2026-08-13.sql | docker compose exec -T db psql -U ispplatform ispplatform

# one-off Django management commands
docker compose exec backend python manage.py <command>
```

---

## Option B: Bare-metal (no Docker)

Use this if you'd rather manage a venv + systemd + host Nginx directly.

```bash
sudo apt update
sudo apt install -y python3-venv python3-dev libpq-dev postgresql nginx certbot python3-certbot-nginx nodejs npm build-essential

# Database
sudo -u postgres psql -c "CREATE USER ispplatform WITH PASSWORD 'CHANGE_ME';"
sudo -u postgres psql -c "CREATE DATABASE ispplatform OWNER ispplatform;"

# App user + code
sudo useradd -r -m -d /opt/isp-platform isp
sudo -u isp git clone <your-repo-url> /opt/isp-platform   # or scp the folder over
cd /opt/isp-platform/backend

# Python deps
sudo -u isp python3 -m venv venv
sudo -u isp ./venv/bin/pip install -r requirements.txt

# Configure
sudo -u isp cp .env.production.example .env
sudo -u isp nano .env   # same fields as the Docker path, but set DB_HOST=localhost

# Migrate + static + (optional) seed
sudo -u isp ./venv/bin/python manage.py migrate
sudo -u isp ./venv/bin/python manage.py collectstatic --noinput
sudo -u isp ./venv/bin/python manage.py createsuperuser

# Frontend build (served as static files by host Nginx)
cd ../frontend
npm install
npm run build   # outputs frontend/dist

# Gunicorn as a systemd service
sudo mkdir -p /var/log/isp-backend && sudo chown isp:isp /var/log/isp-backend
sudo cp ../deploy/isp-backend.service /etc/systemd/system/
sudo nano /etc/systemd/system/isp-backend.service   # fix paths/user if you didn't use /opt/isp-platform or user "isp"
sudo systemctl daemon-reload
sudo systemctl enable --now isp-backend
sudo systemctl status isp-backend

# Host Nginx
sudo cp ../deploy/nginx-vps.conf /etc/nginx/sites-available/isp-platform
sudo nano /etc/nginx/sites-available/isp-platform   # set server_name + root path to frontend/dist
sudo ln -s /etc/nginx/sites-available/isp-platform /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
```

Then, same as the Docker path, once HTTPS is live set
`SECURE_SSL_REDIRECT=True`, `SESSION_COOKIE_SECURE=True`,
`CSRF_COOKIE_SECURE=True`, `BEHIND_HTTPS_PROXY=True` in `.env` and
`sudo systemctl restart isp-backend`.

### Everyday operations (bare-metal path)

```bash
journalctl -u isp-backend -f              # logs
sudo systemctl restart isp-backend        # after changing .env

# redeploy after pulling new code
git pull
./venv/bin/pip install -r requirements.txt
./venv/bin/python manage.py migrate
./venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart isp-backend
cd ../frontend && npm install && npm run build

# database backup
sudo -u postgres pg_dump ispplatform > backup-$(date +%F).sql
```

---

## Demo data

`seed_demo_data` and `simulate_monitoring` (see the main README) create 40
fictional customers, invoices, tickets, and monitoring history — useful for
a first look at a fresh install, not something to leave running against
real customers. For a real launch: don't run them (or if you already did to
try things out, wipe and start clean with `python manage.py seed_demo_data
--flush` run with no further arguments, or just drop and recreate the
database before onboarding real customers).

## Security checklist before going live

- [ ] `DEBUG=False`
- [ ] `SECRET_KEY` is a long random value, not the example one
- [ ] `ALLOWED_HOSTS` lists your real domain(s) only
- [ ] `CORS_ALLOW_ALL_ORIGINS=False` and `CORS_ALLOWED_ORIGINS` set to your real frontend origin
- [ ] Database password is strong and not the `postgres`/`ispplatform` default
- [ ] HTTPS is live and `SECURE_SSL_REDIRECT` / `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` / `BEHIND_HTTPS_PROXY` are `True`
- [ ] Demo data was never seeded against this deploy, or was flushed before go-live
- [ ] You have a working database backup and have tested restoring it
- [ ] `ufw` (or your provider's firewall) allows only 22/80/443

## A note on what I could and couldn't verify

I tested the Django app, the production React build, and Gunicorn serving
the app directly in my own sandbox — all clean. I could not run the actual
`docker build`/`docker compose up` here because this sandbox's network
policy blocks pulling images from Docker Hub; `docker compose config`
confirms the compose file itself is valid, but the image builds are
untested end-to-end. Run `docker compose build && docker compose up -d` as
your first step on the actual VPS and watch `docker compose logs -f` — if
anything doesn't come up cleanly, send me the log output and I'll fix it.
