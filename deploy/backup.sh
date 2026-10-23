#!/usr/bin/env bash
# Skybre platform backups -- package #113, 7 Oct 2026.
#
# Run every hour by the existing cron line:
#   0 * * * * /home/ubuntu/isp-platform/deploy/backup.sh
#
# What it keeps, under ~/backups (local only for now -- an off-site copy is
# still to be added; until then a lost VPS loses the backups too):
#   db/hourly/isp-YYYY-MM-DD-HHMM.dump   every hour, kept 48 hours
#   db/daily/                            first dump of each day, kept 30 days
#   media/media-YYYY-MM-DD.tar.gz        uploads (logo, receipts...), daily, kept 14 days
# A dump is checked readable (pg_restore --list) before it is kept. Log:
# ~/isp-platform/logs/backup.log. The Dashboard's System status card shows
# the newest dump (mounted read-only into the backend at /backups).
#
# RESTORE (stops nothing by itself -- take the platform down first):
#   cd ~/isp-platform
#   docker compose stop backend frontend
#   docker compose exec -T db sh -c 'pg_restore -U "${POSTGRES_USER:-postgres}" \
#       -d "${POSTGRES_DB:-${POSTGRES_USER:-postgres}}" --clean --if-exists --no-owner' \
#       < ~/backups/db/hourly/isp-YYYY-MM-DD-HHMM.dump
#   docker compose start backend frontend
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/home/ubuntu/backups}"
KEEP_HOURLY_HOURS=48
KEEP_DAILY_DAYS=30
KEEP_MEDIA_DAYS=14
MIN_FREE_MB=2048
DOCKER="${DOCKER:-/usr/bin/docker}"
LOG="$PROJECT/logs/backup.log"

mkdir -p "$BACKUP_DIR/db/hourly" "$BACKUP_DIR/db/daily" "$BACKUP_DIR/media" "$PROJECT/logs"
chmod 750 "$BACKUP_DIR" "$BACKUP_DIR/db" "$BACKUP_DIR/db/hourly" "$BACKUP_DIR/db/daily" "$BACKUP_DIR/media"
exec >>"$LOG" 2>&1

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

# One at a time: a slow dump must not be overtaken by the next hour's.
exec 9>"$BACKUP_DIR/.lock"
if ! flock -n 9; then log "skipped: the previous backup is still running"; exit 0; fi

cd "$PROJECT"

free_mb=$(df -Pm "$BACKUP_DIR" | awk 'NR==2 {print $4}')
if [ "$free_mb" -lt "$MIN_FREE_MB" ]; then
  log "FAILED: only ${free_mb} MB free on the backup disk (need ${MIN_FREE_MB} MB). Nothing written."
  exit 1
fi

stamp=$(date +%Y-%m-%d-%H%M)
today=$(date +%Y-%m-%d)

# --- database ----------------------------------------------------------------
out="$BACKUP_DIR/db/hourly/isp-$stamp.dump"
tmp="$out.partial"
if ! "$DOCKER" compose exec -T db sh -c \
    'pg_dump -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-${POSTGRES_USER:-postgres}}" -Fc --no-owner' \
    > "$tmp"; then
  rm -f "$tmp"
  log "FAILED: pg_dump did not finish"
  exit 1
fi
if ! "$DOCKER" compose exec -T db pg_restore --list < "$tmp" > /dev/null; then
  rm -f "$tmp"
  log "FAILED: the dump is not readable by pg_restore -- not kept"
  exit 1
fi
mv "$tmp" "$out"
chmod 640 "$out"
log "database: $(du -h "$out" | cut -f1) $out"

if ! ls "$BACKUP_DIR/db/daily/isp-$today-"*.dump > /dev/null 2>&1; then
  cp -p "$out" "$BACKUP_DIR/db/daily/"
  log "database: kept as today's daily copy"
fi

# --- uploads (logo, receipts, attachments) -------------------------------------
media="$BACKUP_DIR/media/media-$today.tar.gz"
if [ ! -f "$media" ]; then
  if "$DOCKER" compose exec -T backend sh -c 'test -d /app/media && tar czf - -C /app media' > "$media.partial" \
      && [ -s "$media.partial" ]; then
    mv "$media.partial" "$media"
    chmod 640 "$media"
    log "media: $(du -h "$media" | cut -f1) $media"
  else
    rm -f "$media.partial"
    log "media: nothing to back up (no /app/media in the backend container) or tar failed"
  fi
fi

# --- tidy ------------------------------------------------------------------------
find "$BACKUP_DIR/db/hourly" -name 'isp-*.dump' -mmin +$((KEEP_HOURLY_HOURS * 60)) -delete
find "$BACKUP_DIR/db/daily" -name 'isp-*.dump' -mtime +"$KEEP_DAILY_DAYS" -delete
find "$BACKUP_DIR/media" -name 'media-*.tar.gz' -mtime +"$KEEP_MEDIA_DAYS" -delete
find "$BACKUP_DIR" -name '*.partial' -mmin +120 -delete
