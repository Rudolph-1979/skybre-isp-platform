#!/usr/bin/env python3
"""Package #113: hourly database backups (local), and the System status card's
backup row.

* deploy/backup.sh -- run hourly by the cron line that is already there
  (0 * * * * /home/ubuntu/isp-platform/deploy/backup.sh). Dumps the database
  every hour to ~/backups/db/hourly (kept 48 h), keeps the first of each day
  in ~/backups/db/daily (30 days), and the uploads folder daily in
  ~/backups/media (14 days). Each dump is checked readable before it's kept.
  Log: logs/backup.log. Restore steps are at the top of the script.
* docker-compose.override.yml -- mounts ~/backups/db read-only into the
  backend at /backups, for the Dashboard's "Last database backup" row.

Local only for now: an off-site copy is still to be added.

    python3 install_backups.py --check
    python3 install_backups.py
"""
import argparse, os, shutil, sys, time

FILES = {'deploy/backup.sh': '#!/usr/bin/env bash\n# Skybre platform backups -- package #113, 7 Oct 2026.\n#\n# Run every hour by the existing cron line:\n#   0 * * * * /home/ubuntu/isp-platform/deploy/backup.sh\n#\n# What it keeps, under ~/backups (local only for now -- an off-site copy is\n# still to be added; until then a lost VPS loses the backups too):\n#   db/hourly/isp-YYYY-MM-DD-HHMM.dump   every hour, kept 48 hours\n#   db/daily/                            first dump of each day, kept 30 days\n#   media/media-YYYY-MM-DD.tar.gz        uploads (logo, receipts...), daily, kept 14 days\n# A dump is checked readable (pg_restore --list) before it is kept. Log:\n# ~/isp-platform/logs/backup.log. The Dashboard\'s System status card shows\n# the newest dump (mounted read-only into the backend at /backups).\n#\n# RESTORE (stops nothing by itself -- take the platform down first):\n#   cd ~/isp-platform\n#   docker compose stop backend frontend\n#   docker compose exec -T db sh -c \'pg_restore -U "${POSTGRES_USER:-postgres}" \\\n#       -d "${POSTGRES_DB:-${POSTGRES_USER:-postgres}}" --clean --if-exists --no-owner\' \\\n#       < ~/backups/db/hourly/isp-YYYY-MM-DD-HHMM.dump\n#   docker compose start backend frontend\nset -euo pipefail\n\nPROJECT="$(cd "$(dirname "$0")/.." && pwd)"\nBACKUP_DIR="${BACKUP_DIR:-/home/ubuntu/backups}"\nKEEP_HOURLY_HOURS=48\nKEEP_DAILY_DAYS=30\nKEEP_MEDIA_DAYS=14\nMIN_FREE_MB=2048\nDOCKER="${DOCKER:-/usr/bin/docker}"\nLOG="$PROJECT/logs/backup.log"\n\nmkdir -p "$BACKUP_DIR/db/hourly" "$BACKUP_DIR/db/daily" "$BACKUP_DIR/media" "$PROJECT/logs"\nchmod 750 "$BACKUP_DIR" "$BACKUP_DIR/db" "$BACKUP_DIR/db/hourly" "$BACKUP_DIR/db/daily" "$BACKUP_DIR/media"\nexec >>"$LOG" 2>&1\n\nlog() { echo "$(date \'+%Y-%m-%d %H:%M:%S\') $*"; }\n\n# One at a time: a slow dump must not be overtaken by the next hour\'s.\nexec 9>"$BACKUP_DIR/.lock"\nif ! flock -n 9; then log "skipped: the previous backup is still running"; exit 0; fi\n\ncd "$PROJECT"\n\nfree_mb=$(df -Pm "$BACKUP_DIR" | awk \'NR==2 {print $4}\')\nif [ "$free_mb" -lt "$MIN_FREE_MB" ]; then\n  log "FAILED: only ${free_mb} MB free on the backup disk (need ${MIN_FREE_MB} MB). Nothing written."\n  exit 1\nfi\n\nstamp=$(date +%Y-%m-%d-%H%M)\ntoday=$(date +%Y-%m-%d)\n\n# --- database ----------------------------------------------------------------\nout="$BACKUP_DIR/db/hourly/isp-$stamp.dump"\ntmp="$out.partial"\nif ! "$DOCKER" compose exec -T db sh -c \\\n    \'pg_dump -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-${POSTGRES_USER:-postgres}}" -Fc --no-owner\' \\\n    > "$tmp"; then\n  rm -f "$tmp"\n  log "FAILED: pg_dump did not finish"\n  exit 1\nfi\nif ! "$DOCKER" compose exec -T db pg_restore --list < "$tmp" > /dev/null; then\n  rm -f "$tmp"\n  log "FAILED: the dump is not readable by pg_restore -- not kept"\n  exit 1\nfi\nmv "$tmp" "$out"\nchmod 640 "$out"\nlog "database: $(du -h "$out" | cut -f1) $out"\n\nif ! ls "$BACKUP_DIR/db/daily/isp-$today-"*.dump > /dev/null 2>&1; then\n  cp -p "$out" "$BACKUP_DIR/db/daily/"\n  log "database: kept as today\'s daily copy"\nfi\n\n# --- uploads (logo, receipts, attachments) -------------------------------------\nmedia="$BACKUP_DIR/media/media-$today.tar.gz"\nif [ ! -f "$media" ]; then\n  if "$DOCKER" compose exec -T backend sh -c \'test -d /app/media && tar czf - -C /app media\' > "$media.partial" \\\n      && [ -s "$media.partial" ]; then\n    mv "$media.partial" "$media"\n    chmod 640 "$media"\n    log "media: $(du -h "$media" | cut -f1) $media"\n  else\n    rm -f "$media.partial"\n    log "media: nothing to back up (no /app/media in the backend container) or tar failed"\n  fi\nfi\n\n# --- tidy ------------------------------------------------------------------------\nfind "$BACKUP_DIR/db/hourly" -name \'isp-*.dump\' -mmin +$((KEEP_HOURLY_HOURS * 60)) -delete\nfind "$BACKUP_DIR/db/daily" -name \'isp-*.dump\' -mtime +"$KEEP_DAILY_DAYS" -delete\nfind "$BACKUP_DIR/media" -name \'media-*.tar.gz\' -mtime +"$KEEP_MEDIA_DAYS" -delete\nfind "$BACKUP_DIR" -name \'*.partial\' -mmin +120 -delete\n', 'docker-compose.override.yml': "# Package #113 (7 Oct 2026): lets the Dashboard's System status card see the\n# newest database backup. Read-only -- the platform can never change or\n# delete a backup. Docker Compose merges this file with docker-compose.yml\n# automatically.\nservices:\n  backend:\n    volumes:\n      - /home/ubuntu/backups/db:/backups:ro\n"}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if not os.path.isfile("docker-compose.yml"):
        print("REFUSED: run this from ~/isp-platform (docker-compose.yml not found). Nothing written."); return 1
    todo = []
    for path, content in FILES.items():
        if os.path.isfile(path):
            with open(path) as fh:
                current = fh.read()
            if current == content:
                print(f"skip: {path} -- already installed"); continue
            if path == "docker-compose.override.yml":
                print("REFUSED: docker-compose.override.yml already exists with other settings -- send it to "
                      "Claude to merge. Nothing written.")
                return 1
        todo.append((path, content))
    if not todo:
        print("skip: unchanged -- nothing to do")
    if args.check:
        for p, _ in todo: print(f"WOULD WRITE: {p}")
        print("WOULD CREATE: /home/ubuntu/backups/db")
        return 0
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for path, content in todo:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if os.path.isfile(path):
            shutil.copy2(path, f"{path}.prepatch-{stamp}")
            print(f"kept the old one as {path}.prepatch-{stamp}")
        with open(path, "w") as fh:
            fh.write(content)
        print(f"WROTE: {path}")
    os.chmod("deploy/backup.sh", 0o755)
    os.makedirs("/home/ubuntu/backups/db", exist_ok=True)
    print("ready: deploy/backup.sh is executable; /home/ubuntu/backups/db exists")
    return 0


if __name__ == "__main__":
    sys.exit(main())
