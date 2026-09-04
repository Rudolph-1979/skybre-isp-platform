#!/bin/sh
set -e

echo "Waiting for database at ${DB_HOST:-db}:${DB_PORT:-5432}..."
python - <<'PYEOF'
import os, sys, time
import psycopg2

host = os.environ.get("DB_HOST", "db")
port = os.environ.get("DB_PORT", "5432")
name = os.environ.get("DB_NAME", "ispplatform")
user = os.environ.get("DB_USER", "postgres")
password = os.environ.get("DB_PASSWORD", "postgres")

for attempt in range(60):
    try:
        conn = psycopg2.connect(host=host, port=port, dbname=name, user=user, password=password)
        conn.close()
        print("Database is ready.")
        sys.exit(0)
    except psycopg2.OperationalError:
        time.sleep(1)

print("Database never became ready, exiting.")
sys.exit(1)
PYEOF

# Check the settings BEFORE migrating, and say something useful if they
# are wrong. settings.py refuses to import with DEBUG=False while
# ALLOWED_HOSTS is still "*" or CORS_ALLOW_ALL_ORIGINS is still True --
# both correct refusals, but with `set -e` here and
# `restart: unless-stopped` in compose, an unexplained one is a crash loop
# that looks like the image is broken. This turns it into one banner in
# `docker compose logs backend` naming the variable to set.
# `check --deploy` exits 0 on its security WARNINGS (W004 HSTS, W008 SSL
# redirect, W012/W016 cookie flags...) and non-zero only on errors, so it
# is safe as a gate. The output is printed either way rather than
# swallowed -- those warnings are the deployment hardening checklist, and
# hiding them in a temp file is how they stay unfixed.
echo "Checking configuration..."
# Not a pipeline: in /bin/sh a pipeline's status is the LAST command's, so
# `check | tee` would always look successful and this gate would never
# fire. Captured, status kept, then printed.
set +e
python manage.py check --deploy > /tmp/check.out 2>&1
check_status=$?
set -e
cat /tmp/check.out
if [ "$check_status" -ne 0 ]; then
  echo "============================================================"
  echo " The backend refused to start because of its configuration."
  echo " Fix backend/.env and bring it up again -- Django's reason is"
  echo " immediately above."
  echo "------------------------------------------------------------"
  echo " Most likely, backend/.env is missing these (required once"
  echo " DEBUG=False -- see backend/.env.production.example):"
  echo "   ALLOWED_HOSTS=portal.skybre.co.za,<vps-ip>"
  echo "   CORS_ALLOW_ALL_ORIGINS=False"
  echo "   CORS_ALLOWED_ORIGINS=https://portal.skybre.co.za"
  echo "============================================================"
  exit 1
fi

echo "Running migrations..."
python manage.py migrate --noinput

echo "Collecting static files..."
python manage.py collectstatic --noinput

if [ "${SEED_DEMO_DATA:-false}" = "true" ]; then
  echo "Seeding demo data (SEED_DEMO_DATA=true)..."
  python manage.py seed_demo_data
  python manage.py simulate_monitoring --hours 48 --interval-minutes 15
fi

echo "Starting Gunicorn..."
exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers "${GUNICORN_WORKERS:-3}" \
  --access-logfile - \
  --error-logfile -
