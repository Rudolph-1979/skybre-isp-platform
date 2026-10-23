#!/usr/bin/env bash
# Webfleet switch-on for Vexar (package #186), 16 Oct 2026.
#
# Run ON THE SERVER:
#     cd ~/isp-platform && bash setup_webfleet.sh
#
# Asks for the Webfleet account name, username, password and API key
# (password and key typed hidden; Enter keeps a value already in
# backend/.env), writes them there (one line each, no duplicates), restarts the backend, runs the first sync,
# and offers to add the 15-minute crontab line. Safe to run again.
# No secret is stored in this file or in the shell history.
set -euo pipefail

cd ~/isp-platform
ENV=backend/.env
[ -f "$ENV" ] || { echo "Can't find $ENV -- run this from ~/isp-platform on the server."; exit 1; }

setval() {   # setval KEY VALUE -- replace or add KEY=VALUE, keeping the file's owner/permissions
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  grep -v "^${key}=" "$ENV" > "$tmp" || true
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  cat "$tmp" > "$ENV"
  rm -f "$tmp"
}

current() { grep "^$1=" "$ENV" | tail -1 | cut -d= -f2- || true; }

echo "== Webfleet details =="
echo "(Anything already in backend/.env is kept if you just press Enter.)"
OLD_ACCOUNT="$(current WEBFLEET_ACCOUNT)"
OLD_USERNAME="$(current WEBFLEET_USERNAME)"
OLD_PASSWORD="$(current WEBFLEET_PASSWORD)"
OLD_APIKEY="$(current WEBFLEET_API_KEY)"

read -r -p "Webfleet ACCOUNT name (first box on the Webfleet login screen)${OLD_ACCOUNT:+ [keep: $OLD_ACCOUNT]}: " ACCOUNT
ACCOUNT="${ACCOUNT:-$OLD_ACCOUNT}"
read -r -p "Webfleet username [${OLD_USERNAME:-vexar}]: " USERNAME
USERNAME="${USERNAME:-${OLD_USERNAME:-vexar}}"
read -r -s -p "Password of the ${USERNAME} user (hidden)${OLD_PASSWORD:+ [Enter = keep the one already set]}: " PASSWORD; echo
PASSWORD="${PASSWORD:-$OLD_PASSWORD}"
# Pasting into a hidden prompt can bring along invisible characters (the
# terminal's paste markers ESC[200~ / ESC[201~, a carriage return, spaces),
# which made the first try fail with Webfleet error 1143. They are removed,
# and the key must look like a Webfleet key before anything is written.
clean() { printf '%s' "$1" | sed -e $'s/\x1b\[20[01]~//g' | tr -d '\r\n\t '; }
while true; do
  read -r -s -p "Webfleet API key (hidden)${OLD_APIKEY:+ [Enter = keep the one already set]}: " APIKEY; echo
  APIKEY="$(clean "$APIKEY")"
  APIKEY="${APIKEY:-$OLD_APIKEY}"
  if [[ "$APIKEY" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; then
    echo "API key looks right (${#APIKEY} characters, ends ...${APIKEY: -4})."
    break
  fi
  echo "That isn't a Webfleet API key (expected 8-4-4-4-12 characters, got ${#APIKEY}). Paste it again."
  OLD_APIKEY=""
done
unset OLD_PASSWORD OLD_APIKEY

for pair in "ACCOUNT:$ACCOUNT" "PASSWORD:$PASSWORD" "API key:$APIKEY"; do
  [ -n "${pair#*:}" ] || { echo "The ${pair%%:*} is empty -- nothing written."; exit 1; }
done

setval WEBFLEET_ACCOUNT "$ACCOUNT"
setval WEBFLEET_USERNAME "$USERNAME"
setval WEBFLEET_PASSWORD "$PASSWORD"
setval WEBFLEET_API_KEY "$APIKEY"
unset PASSWORD APIKEY
echo "Written: $(grep -c '^WEBFLEET_' "$ENV") WEBFLEET_ settings in $ENV (should be 4)."

echo "== Restarting the backend =="
docker compose up -d backend
for i in $(seq 1 30); do
  if docker compose exec -T backend python -c "print()" >/dev/null 2>&1; then break; fi
  sleep 2
done

echo "== First Webfleet sync (vehicles, odometer, trips, OptiDrive) =="
OUT="$(docker compose exec -T backend python manage.py sync_webfleet --kpis 2>&1)" || true
echo "$OUT"
if grep -qiE "webfleet error|couldn't reach|isn't set up|limit reached" <<<"$OUT"; then
  echo "Sync reported a problem (above). Send it to Claude."
else
  echo "Sync OK."
fi

CRON='*/15 * * * * cd ~/isp-platform && docker compose exec -T backend python manage.py sync_webfleet'
if crontab -l 2>/dev/null | grep -q 'sync_webfleet'; then
  echo "Crontab already has the sync_webfleet line."
else
  read -r -p "Add the every-15-minutes sync to crontab now? [y/N] " ADD
  if [[ "${ADD,,}" == "y" ]]; then
    (crontab -l 2>/dev/null; echo "$CRON") | crontab -
    echo "Added: $CRON"
  else
    echo "Not added. Later: crontab -e and add:"
    echo "  $CRON"
  fi
fi
echo "Done. Ctrl+F5 in the browser, then Networking -> Map -> 'Vehicles (Webfleet)'."
