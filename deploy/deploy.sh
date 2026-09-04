#!/usr/bin/env bash
#
# Deploy the platform on the VPS, in an order that fails safely.
#
# Run from the project directory on the server:
#
#     ./deploy/deploy.sh            # check, migrate, build, start, verify
#     ./deploy/deploy.sh --check    # check only, change nothing
#
# Why a script rather than the four commands in DEPLOYMENT.md:
#
#   * The documented order ran `migrate` BEFORE `build`. The running
#     container has the previous build's code baked in, so the new
#     migration files are not in it -- that step could not apply them.
#     The migrations actually ran later, ungated, from entrypoint.sh at
#     `up -d`, where a failure meets `set -e` and `restart: unless-stopped`
#     and becomes a crash loop instead of a message. Here migrate is an
#     explicit step, after the build and before anything serves traffic,
#     so a failure stops the deploy with the error on screen and the old
#     container still running.
#
#   * settings.py refuses to import with DEBUG=False while ALLOWED_HOSTS
#     is "*" or CORS_ALLOW_ALL_ORIGINS is True. Both refusals are correct,
#     and both are much easier to act on before a rebuild than as a
#     restarting container.
#
#   * It takes a database backup first. Two of the pending migrations
#     alter billing tables.
#
# This does NOT git pull. Deploys here are scp'd zips by design --
# docker-compose.yml on the box has diverged from git before -- so this
# script builds whatever is in the directory you run it from.
set -euo pipefail

cd "$(dirname "$0")/.."
CHECK_ONLY="${1:-}"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }

# --- 1. the things that make a deploy fail at 2am ----------------------
say "Checking prerequisites"

[ -f backend/.env ] || fail "backend/.env is missing. Copy backend/.env.production.example and fill it in."

missing=""
for key in SECRET_KEY ALLOWED_HOSTS DB_NAME DB_USER DB_PASSWORD; do
  grep -qE "^${key}=." backend/.env || missing="${missing} ${key}"
done
[ -z "$missing" ] || fail "backend/.env is missing values for:${missing}"

# The two that stop the container importing at all.
if grep -qE '^DEBUG=False' backend/.env; then
  grep -qE '^ALLOWED_HOSTS=\*?$' backend/.env && \
    fail 'ALLOWED_HOSTS is "*" (or empty) while DEBUG=False. Set it to the real hostnames.'
  grep -qiE '^CORS_ALLOW_ALL_ORIGINS=(True|1|yes)' backend/.env && \
    fail 'CORS_ALLOW_ALL_ORIGINS is True while DEBUG=False. Set it to False and list CORS_ALLOWED_ORIGINS.'
fi

grep -qiE '^SEED_DEMO_DATA=(True|1|yes)' backend/.env && \
  fail 'SEED_DEMO_DATA is true. That resets the admin password and injects fictional customers on every start.'

# Postgres must not be reachable from anywhere but this host. Docker
# publishes ports by DNAT, which never traverses the INPUT chain ufw's
# default-deny lives in -- so a 0.0.0.0 binding here is open to the
# internet no matter what `ufw status` says, and this database holds every
# subscriber's internet password in plaintext.
if docker compose ps --format '{{.Service}} {{.Ports}}' 2>/dev/null | grep -qE '0\.0\.0\.0:5432'; then
  fail "Postgres is published on 0.0.0.0:5432. docker-compose.yml in git binds it to 127.0.0.1 -- the running config has diverged. Fix it before deploying."
fi

echo "  prerequisites OK"

if [ "$CHECK_ONLY" = "--check" ]; then
  say "Check only -- nothing changed."
  exit 0
fi

# --- 2. backup ---------------------------------------------------------
say "Backing up the database first"
if [ -x deploy/backup.sh ]; then
  ./deploy/backup.sh || fail "Backup failed. Not deploying on top of an unbacked-up database."
else
  stamp="$(date +%Y%m%d-%H%M%S)"
  mkdir -p ~/backups/pre-deploy
  docker compose exec -T db pg_dump -U "$(grep -E '^DB_USER=' backend/.env | cut -d= -f2)" \
      "$(grep -E '^DB_NAME=' backend/.env | cut -d= -f2)" \
      | gzip > ~/backups/pre-deploy/"predeploy-${stamp}.sql.gz" \
    || fail "Backup failed. Not deploying on top of an unbacked-up database."
  echo "  wrote ~/backups/pre-deploy/predeploy-${stamp}.sql.gz"
fi

# --- 3. build, then migrate, then serve --------------------------------
say "Building images"
docker compose build backend frontend

say "Applying migrations (explicit step, on the NEW code, before anything serves)"
docker compose run --rm backend python manage.py migrate --noinput \
  || fail "Migration failed. Nothing has been restarted -- the old containers are still serving."

say "Starting"
docker compose up -d

# --- 4. verify ---------------------------------------------------------
say "Waiting for the backend to report healthy"
for _ in $(seq 1 30); do
  state="$(docker compose ps --format '{{.Service}} {{.Health}}' 2>/dev/null | awk '$1=="backend"{print $2}')"
  [ "$state" = "healthy" ] && break
  sleep 3
done
[ "${state:-}" = "healthy" ] || {
  echo "--- backend logs ---"; docker compose logs backend --tail 40
  fail "Backend never became healthy."
}

say "Deployed"
docker compose ps
echo
echo "Worth doing now:"
echo "  * Reconcile anything the router never heard about:"
echo "      docker compose exec backend python manage.py resync_radius        # dry run"
echo "      docker compose exec backend python manage.py resync_radius --kick"
echo "  * Size the ledger drift (read-only, writes nothing):"
echo "      docker compose exec backend python manage.py balance_drift --csv /tmp/drift.csv"
