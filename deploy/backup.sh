#!/usr/bin/env bash
#
# Hourly backup for the Skybre ISP platform.
#
#  - Dumps the Postgres database from the running `db` container.
#  - Keeps a rolling local history: the last 48 hourly backups (2 days)
#    plus one daily snapshot per day for 30 days.
#  - Optionally copies each backup to Google Cloud Storage, once
#    GCS_BUCKET and GCS_KEY_FILE below are configured — safe to leave
#    unset for now; backups still happen locally either way.
#
# Install: run hourly via cron. See BACKUP.md for full setup.

set -euo pipefail

# ---- configuration ----------------------------------------------------
PROJECT_DIR="/home/ubuntu/isp-platform"
BACKUP_ROOT="/home/ubuntu/backups"
HOURLY_DIR="$BACKUP_ROOT/hourly"
DAILY_DIR="$BACKUP_ROOT/daily"
LOG_FILE="$BACKUP_ROOT/backup.log"

KEEP_HOURLY=48   # 2 days of hourly backups
KEEP_DAILY=30    # 30 days of daily backups

# Fill these in once you've created a GCS bucket + service account key
# (see BACKUP.md) to switch on off-site copying. Leave GCS_BUCKET blank
# to skip it entirely.
GCS_BUCKET=""
GCS_KEY_FILE="$BACKUP_ROOT/gcs-service-account.json"
# -------------------------------------------------------------------------

mkdir -p "$HOURLY_DIR" "$DAILY_DIR"

timestamp="$(date +%Y-%m-%d_%H-%M)"
today="$(date +%Y-%m-%d)"
hour="$(date +%H)"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >>"$LOG_FILE"; }

cd "$PROJECT_DIR"

DB_USER=$(grep -E '^DB_USER=' backend/.env | cut -d'=' -f2- | tr -d '\r')
DB_NAME=$(grep -E '^DB_NAME=' backend/.env | cut -d'=' -f2- | tr -d '\r')

dump_file="$HOURLY_DIR/skybre_${timestamp}.sql.gz"

if docker compose exec -T db pg_dump -U "$DB_USER" "$DB_NAME" | gzip >"$dump_file.tmp"; then
    mv "$dump_file.tmp" "$dump_file"
    log "OK: hourly backup created ($(du -h "$dump_file" | cut -f1))"
else
    rm -f "$dump_file.tmp"
    log "FAILED: pg_dump did not complete — no backup written this hour"
    exit 1
fi

if [ "$hour" = "00" ] || [ ! -f "$DAILY_DIR/skybre_${today}.sql.gz" ]; then
    cp "$dump_file" "$DAILY_DIR/skybre_${today}.sql.gz"
fi

prune() {
    local dir="$1" keep="$2"
    ls -1t "$dir"/skybre_*.sql.gz 2>/dev/null | tail -n +"$((keep + 1))" | xargs -r rm -f
}
prune "$HOURLY_DIR" "$KEEP_HOURLY"
prune "$DAILY_DIR" "$KEEP_DAILY"

if [ -n "$GCS_BUCKET" ] && [ -f "$GCS_KEY_FILE" ]; then
    if ! command -v gcloud >/dev/null 2>&1; then
        log "WARNING: GCS configured but gcloud CLI not installed — skipping off-site upload"
    else
        gcloud auth activate-service-account --key-file="$GCS_KEY_FILE" >/dev/null 2>&1
        if gcloud storage cp "$dump_file" "gs://$GCS_BUCKET/hourly/$(basename "$dump_file")" >/dev/null 2>&1; then
            log "OK: uploaded to gs://$GCS_BUCKET/hourly/"
        else
            log "WARNING: off-site upload failed — local backup is still safe, will retry next hour"
        fi
        if [ -f "$DAILY_DIR/skybre_${today}.sql.gz" ]; then
            gcloud storage cp "$DAILY_DIR/skybre_${today}.sql.gz" "gs://$GCS_BUCKET/daily/skybre_${today}.sql.gz" >/dev/null 2>&1 || true
        fi
    fi
else
    log "INFO: off-site backup not configured yet (set GCS_BUCKET + GCS_KEY_FILE in backup.sh — see BACKUP.md)"
fi

log "Backup run complete."
