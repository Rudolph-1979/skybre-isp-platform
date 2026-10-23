#!/usr/bin/env bash
# Skybre / Vexar off-site backup -- package #198, 19 Oct 2026.
#
# Pushes encrypted backups from this VPS to Rudolph's Google Drive folder
# ("Vexar backups", the rclone remote set up by deploy/setup_offsite.sh).
# Runs as ROOT (it reads /etc/freeradius, /etc/openvpn and /etc/letsencrypt):
#
#   --db     every hour: the newest database dump, encrypted -> db-hourly/
#            (kept 48 hours on Drive; ~48 MB each on 19 Oct 2026)
#   --full   every night: the whole REBUILD KIT, encrypted -> kits/
#            (database, uploads, the Vexar code incl. .env + compose files,
#             FreeRADIUS, OpenVPN, nginx, Let's Encrypt, systemd units,
#             firewall rules, crontabs and an inventory with the counts)
#   --test   upload, list and delete a small test file (used by setup)
#
# A kit is restored on a fresh Ubuntu 24.04 server with deploy/restore.sh.
# Everything is encrypted with the passphrase in /root/.skybre/kit-pass
# (gpg, AES-256) BEFORE it leaves the server -- Google only ever holds
# encrypted files. Without that passphrase the backups cannot be opened,
# so it must also be kept in a password manager.
#
# After every successful upload ~/backups/db/.remote-backup-ok is touched,
# which the Dashboard's System status card shows as the off-site backup
# (red when older than 26 hours). Log: ~/isp-platform/logs/offsite.log
set -uo pipefail

CONF=/root/.skybre/offsite.conf
[ -r "$CONF" ] || { echo "No $CONF -- run deploy/setup_offsite.sh first."; exit 1; }
# shellcheck source=/dev/null
. "$CONF"
: "${REMOTE:?REMOTE missing in $CONF}" "${PASSFILE:?PASSFILE missing in $CONF}"
PROJECT="${PROJECT:-/home/ubuntu/isp-platform}"
BACKUP_DIR="${BACKUP_DIR:-/home/ubuntu/backups}"
KEEP_KIT_DAYS="${KEEP_KIT_DAYS:-30}"
# Hourly dumps are ~48 MB (19 Oct): 48 hours of them is ~2.3 GB on Drive.
KEEP_DB_HOURS="${KEEP_DB_HOURS:-48}"
KEEP_LOCAL_KITS="${KEEP_LOCAL_KITS:-2}"
LOCAL_KITS=/root/skybre-kits
STAMP_FILE="$BACKUP_DIR/db/.remote-backup-ok"
DOCKER="${DOCKER:-/usr/bin/docker}"
RCLONE="${RCLONE:-/usr/bin/rclone}"
LOG="$PROJECT/logs/offsite.log"
MIN_FREE_MB=1024

mkdir -p "$PROJECT/logs" "$LOCAL_KITS"
touch "$LOG" && chmod 644 "$LOG"   # no secrets in it; readable without sudo
chmod 700 "$LOCAL_KITS"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }
fail() { log "FAILED ($MODE): $*"; exit 1; }

MODE="${1:-}"
case "$MODE" in --db|--full|--test) ;; *) echo "Usage: $0 --db | --full | --test"; exit 2 ;; esac
[ "$(id -u)" = 0 ] || { echo "Run as root (sudo)."; exit 1; }
[ -s "$PASSFILE" ] || fail "no passphrase in $PASSFILE"

# One job at a time; the hourly run waits up to 20 min for a nightly kit.
exec 9>/run/skybre-offsite.lock
flock -w 1200 9 || fail "another off-site backup is still running"

free_mb=$(df -Pm /root | awk 'NR==2 {print $4}')
[ "$free_mb" -ge "$MIN_FREE_MB" ] || fail "only ${free_mb} MB free (need ${MIN_FREE_MB} MB)"

rc() { "$RCLONE" --retries 3 --low-level-retries 10 --contimeout 30s --timeout 5m "$@"; }

encrypt() {  # encrypt <in or -> <out>
  gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-file "$PASSFILE" \
      --symmetric --cipher-algo AES256 --compress-algo none -o "$2" "$1"
}

upload() {  # upload <local file> <remote dir> -- then checks the size on Drive
  local f="$1" dir="$2" name size remote_size
  name=$(basename "$f")
  size=$(stat -c %s "$f")
  local t0=$SECONDS
  rc copyto "$f" "${REMOTE}${dir}/${name}" || return 1
  remote_size=$(rc lsf --format s --files-only "${REMOTE}${dir}/${name}" 2>/dev/null | head -1)
  [ "$remote_size" = "$size" ] || { log "size on Drive ($remote_size) != local ($size) for $dir/$name"; return 1; }
  log "uploaded $dir/$name ($(numfmt --to=iec "$size") in $((SECONDS - t0)) s)"
}

stamp_ok() {
  touch "$STAMP_FILE" 2>/dev/null && chown --reference="$BACKUP_DIR/db" "$STAMP_FILE" 2>/dev/null
  return 0
}

prune_remote() {  # prune_remote <dir> <age e.g. 48h / 30d> <pattern>
  # Deleted for good, not to Drive's bin: binned files still count against
  # the Drive quota for 30 days (a month of hourly dumps would be ~35 GB).
  rc delete "${REMOTE}$1" --min-age "$2" --include "$3" --drive-use-trash=false 2>>"$LOG" \
    || log "note: could not tidy old files in $1 (kept)"
}

dump_db() {  # dump_db <out> -- fresh pg_dump, checked readable
  cd "$PROJECT" || return 1
  "$DOCKER" compose exec -T db sh -c \
    'pg_dump -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-${POSTGRES_USER:-postgres}}" -Fc --no-owner' > "$1" \
    && [ -s "$1" ] \
    && "$DOCKER" compose exec -T db pg_restore --list < "$1" > /dev/null
}

# ---------------------------------------------------------------------------
if [ "$MODE" = --test ]; then
  t="$LOCAL_KITS/connection-test-$(date +%Y%m%d%H%M%S).txt"
  echo "Vexar off-site connection test $(date -Is) from $(hostname)" > "$t"
  encrypt "$t" "$t.gpg" || fail "gpg could not encrypt"
  upload "$t.gpg" test || { rm -f "$t" "$t.gpg"; fail "could not upload to Google Drive -- check the rclone remote ($REMOTE)"; }
  rc deletefile "${REMOTE}test/$(basename "$t.gpg")" || log "note: test file left on Drive in test/"
  rm -f "$t" "$t.gpg"
  log "test OK: Google Drive reachable, upload + size check + delete worked"
  exit 0
fi

# ---------------------------------------------------------------------------
if [ "$MODE" = --db ]; then
  newest=$(ls -t "$BACKUP_DIR"/db/hourly/isp-*.dump 2>/dev/null | head -1 || true)
  work=$(mktemp -d /root/offsite-db.XXXXXX)
  trap 'rm -rf "$work"' EXIT
  if [ -n "$newest" ] && [ -n "$(find "$newest" -mmin -90 2>/dev/null)" ]; then
    src="$newest"
  else
    src="$work/isp-$(date +%Y-%m-%d-%H%M).dump"
    log "no hourly dump younger than 90 min -- taking a fresh one"
    dump_db "$src" || fail "pg_dump failed"
  fi
  out="$work/$(basename "$src").gpg"
  encrypt "$src" "$out" || fail "gpg could not encrypt $src"
  upload "$out" db-hourly || fail "upload of $(basename "$out") failed"
  prune_remote db-hourly "${KEEP_DB_HOURS}h" 'isp-*.dump.gpg'
  stamp_ok
  exit 0
fi

# ---------------------------------------------------------------------------
# --full: the rebuild kit
stamp=$(date +%Y-%m-%d-%H%M)
work=$(mktemp -d /root/kit-work.XXXXXX)
trap 'rm -rf "$work"' EXIT
kit="$work/kit"
mkdir -p "$kit/db" "$kit/media" "$kit/app" "$kit/host"
cd "$PROJECT" || fail "no $PROJECT"

dump_db "$kit/db/isp.dump" || fail "pg_dump failed"
log "kit: database $(du -h "$kit/db/isp.dump" | cut -f1)"

# Uploads. media/terrain (#179) is left out: ~210 MB that `manage.py
# fetch_terrain` downloads again.
if "$DOCKER" compose exec -T backend sh -c 'test -d /app/media && tar czf - -C /app --exclude=media/terrain media' \
    > "$kit/media/media.tar.gz" && [ -s "$kit/media/media.tar.gz" ]; then
  log "kit: media $(du -h "$kit/media/media.tar.gz" | cut -f1)"
else
  rm -f "$kit/media/media.tar.gz"
  log "kit: no media (nothing in /app/media, or tar failed)"
fi

# The code as it runs here, with backend/.env, frontend/.env and the
# compose files. Not node_modules / builds / caches / patch backups / logs.
tar czf "$kit/app/isp-platform.tar.gz" -C "$(dirname "$PROJECT")" \
    --exclude="$(basename "$PROJECT")/frontend/node_modules" \
    --exclude="$(basename "$PROJECT")/frontend/dist" \
    --exclude="$(basename "$PROJECT")/logs" \
    --exclude="$(basename "$PROJECT")/.git" \
    --exclude='__pycache__' --exclude='*.prepatch-*' \
    --exclude="$(basename "$PROJECT")/*.tgz" --exclude="$(basename "$PROJECT")/*.zip" \
    "$(basename "$PROJECT")" || fail "could not pack $PROJECT"
log "kit: code $(du -h "$kit/app/isp-platform.tar.gz" | cut -f1)"

# Host configuration that lives outside the code (REBUILD.md section 1.5).
host_paths=()
for p in /etc/freeradius/3.0 /etc/openvpn /etc/nginx/sites-available /etc/nginx/sites-enabled /etc/letsencrypt \
         /etc/ufw/user.rules /etc/ufw/user6.rules /var/lib/skybre; do
  [ -e "$p" ] && host_paths+=("${p#/}")
done
for unit in /etc/systemd/system/skybre-* /etc/systemd/system/openvpn-server@*.service.d; do
  [ -e "$unit" ] || continue
  host_paths+=("${unit#/}")
  # The helper script a unit runs (e.g. the RADIUS clients.conf installer).
  if [ -f "$unit" ]; then
    for exe in $(sed -n 's/^ExecStart[A-Za-z]*=[-@+!]*\([^ ]*\).*/\1/p' "$unit"); do
      case "$exe" in /usr/bin/*|/bin/*|/usr/sbin/*|/sbin/*) ;; /*) [ -f "$exe" ] && host_paths+=("${exe#/}") ;; esac
    done
  fi
done
if [ "${#host_paths[@]}" -gt 0 ]; then
  tar czf "$kit/host/etc.tar.gz" -C / "${host_paths[@]}" 2>>"$LOG" || fail "could not pack the host configuration"
else
  fail "none of the host configuration paths exist (/etc/freeradius/3.0, /etc/openvpn ...) -- is this the Vexar server?"
fi
printf '%s\n' "${host_paths[@]}" > "$kit/host/paths.txt"
crontab -l -u ubuntu > "$kit/host/crontab-ubuntu.txt" 2>/dev/null || true
crontab -l > "$kit/host/crontab-root.txt" 2>/dev/null || true
log "kit: host config ${#host_paths[@]} paths"

# Inventory -- what restore.sh compares against.
counts=$("$DOCKER" compose exec -T backend python manage.py shell -c \
  "from customers.models import Customer; from billing.models import Service, Invoice, Payment; print(f'customers={Customer.objects.count()} services={Service.objects.count()} invoices={Invoice.objects.count()} payments={Payment.objects.count()}')" 2>/dev/null | tail -1)
{
  echo "KIT_CREATED=$(date -Is)"
  echo "SOURCE_HOST=$(hostname)"
  echo "SOURCE_IPS=$(hostname -I 2>/dev/null | xargs)"
  echo "TIMEZONE=$(timedatectl show -p Timezone --value 2>/dev/null)"
  echo "OS=$(. /etc/os-release; echo "$PRETTY_NAME")"
  echo "PROJECT=$PROJECT"
  echo "COUNTS $counts"
  echo "VOLUMES $("$DOCKER" volume ls --format '{{.Name}}' | xargs)"
  echo "SERVICES $("$DOCKER" compose config --services 2>/dev/null | xargs)"
  echo "OPENVPN_UNITS $(systemctl list-units --all --plain --no-legend 'openvpn-server@*' 2>/dev/null | awk '{print $1}' | xargs)"
  echo "ENV_KEYS $(sed -E 's/=.*//' "$PROJECT/backend/.env" | grep -Ev '^\s*(#|$)' | xargs)"
  echo "MIGRATIONS_APPLIED $("$DOCKER" compose exec -T backend python manage.py showmigrations 2>/dev/null | grep -c '\[X\]')"
  echo "PACKAGES $(dpkg-query -W -f='${Package}=${Version} ' freeradius openvpn nginx certbot docker-ce docker.io docker-compose-v2 docker-compose-plugin 2>/dev/null)"
} > "$kit/inventory.txt"

cat > "$kit/KIT_README.txt" <<README
Vexar / Skybre rebuild kit, made $(date '+%d %b %Y %H:%M') on $(hostname).

Restore on a fresh Ubuntu 24.04 server (copy this .gpg file there first):
    sudo bash restore.sh --test skybre-kit-$stamp.tar.gpg    (practice run, nothing goes out)
    sudo bash restore.sh --live skybre-kit-$stamp.tar.gpg    (real rebuild)
restore.sh is inside app/isp-platform.tar.gz (isp-platform/deploy/restore.sh)
and in G:\\Projects\\ISP Management. Full background: REBUILD.md.

db/isp.dump              pg_dump -Fc of the whole database
media/media.tar.gz       /app/media (uploads), without media/terrain
app/isp-platform.tar.gz  the code + backend/.env + frontend/.env + compose files + deploy/
host/etc.tar.gz          FreeRADIUS, OpenVPN, nginx sites, Let's Encrypt, systemd units, ufw rules
host/crontab-*.txt       crontabs of ubuntu and root
inventory.txt            counts, volumes, timezone, packages of the source server
README

(cd "$kit" && find . -type f ! -name MANIFEST.sha256 -print0 | sort -z | xargs -0 sha256sum > MANIFEST.sha256)

out="$LOCAL_KITS/skybre-kit-$stamp.tar.gpg"
tar cf - -C "$work" kit | encrypt - "$out" || { rm -f "$out"; fail "gpg could not encrypt the kit"; }
chmod 600 "$out"
log "kit: encrypted $(du -h "$out" | cut -f1) $out ($counts)"
upload "$out" kits || fail "upload of $(basename "$out") failed (kept locally in $LOCAL_KITS)"
prune_remote kits "${KEEP_KIT_DAYS}d" 'skybre-kit-*.tar.gpg'
# Keep the newest few locally too (root-only).
ls -t "$LOCAL_KITS"/skybre-kit-*.tar.gpg 2>/dev/null | tail -n +$((KEEP_LOCAL_KITS + 1)) | xargs -r rm -f
stamp_ok
log "kit OK"
