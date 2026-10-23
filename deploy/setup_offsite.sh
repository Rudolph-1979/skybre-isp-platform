#!/usr/bin/env bash
# Switch on the off-site backups to Google Drive -- package #198, 19 Oct 2026.
#
# Run ON THE SERVER, once:
#     cd ~/isp-platform && sudo bash deploy/setup_offsite.sh
#
# 1. installs rclone + gpg
# 2. connects rclone to the Google Drive folder (you paste the token that
#    `rclone authorize "drive"` gives you on your PC -- typed hidden)
# 3. sets the backup passphrase (typed hidden, stored root-only in
#    /root/.skybre/kit-pass) -- ALSO put it in your password manager: without
#    it the backups cannot be opened
# 4. tests the connection, sends the first database backup
# 5. offers the two root crontab lines (hourly database, nightly rebuild kit)
# Safe to run again: Enter keeps what is already set.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo: sudo bash deploy/setup_offsite.sh"; exit 1; }
cd "$(dirname "$0")/.."
PROJECT="$(pwd)"
SCRIPT="$PROJECT/deploy/offsite_backup.sh"
[ -x "$SCRIPT" ] || chmod 755 "$SCRIPT"
DEFAULT_FOLDER="1o-EDC2nJaWTwHzKIY8R5hLY1A5yxhStZ"
mkdir -p /root/.skybre && chmod 700 /root/.skybre
RCONF=/root/.config/rclone/rclone.conf

echo "== 1. rclone + gpg"
if ! command -v rclone >/dev/null || ! command -v gpg >/dev/null; then
  apt-get update -qq && apt-get install -y -qq rclone gnupg
fi
rclone version | head -1

# Paste into a hidden prompt can carry the terminal's paste markers and a CR.
clean() { printf '%s' "$1" | sed -e $'s/\x1b\[20[01]~//g' | tr -d '\r\n\t'; }

echo
echo "== 2. Google Drive"
if rclone listremotes 2>/dev/null | grep -qx 'gdrive:'; then
  echo "rclone remote 'gdrive' already exists (folder $(rclone config show gdrive 2>/dev/null | sed -n 's/^root_folder_id = //p'))."
  read -r -p "Keep it? [Y/n] " KEEP
else
  KEEP=n
fi
if [[ "${KEEP,,}" == n* ]]; then
  read -r -p "Google Drive folder link or ID [$DEFAULT_FOLDER]: " FOLDER
  FOLDER="$(clean "${FOLDER:-$DEFAULT_FOLDER}")"
  FOLDER="${FOLDER%%\?*}"; FOLDER="${FOLDER##*/}"
  [[ "$FOLDER" =~ ^[A-Za-z0-9_-]{10,}$ ]] || { echo "That doesn't look like a Drive folder ID: $FOLDER"; exit 1; }
  cat <<'TXT'

On your PC (PowerShell), not here:
  1. Download rclone for Windows from https://rclone.org/downloads/ (Windows, Intel/AMD 64 bit),
     unzip it, and in PowerShell go into the unzipped folder (cd ...\rclone-v1.xx-windows-amd64).
  2. Run:   .\rclone.exe authorize "drive"
  3. A browser opens: sign in with the Google account that owns the "Vexar backups" folder
     and click Allow.
  4. PowerShell then prints a line starting with {"access_token": ... ending with }
     Copy that whole line (from { to }) and paste it below.
TXT
  TOKEN_FILE=/home/ubuntu/gdrive-token.txt
  echo "(Or, instead of pasting: save it on your PC into a file and copy that to $TOKEN_FILE,"
  echo " then just press Enter here.)"
  # A long line copied out of a PowerShell window can arrive broken over
  # several lines: keep reading (hidden) until the closing } has arrived.
  TOKEN=""
  read -r -s -p "Paste the token line (hidden): " LINE || true; echo
  TOKEN="$LINE"
  while [ -n "$TOKEN" ] && [[ "$TOKEN" == *"{"* ]] && [[ "$TOKEN" != *"}"* ]]; do
    read -r -s -t 5 LINE || break
    TOKEN="$TOKEN$LINE"
  done
  if [ -z "$TOKEN" ] && [ -s "$TOKEN_FILE" ]; then
    TOKEN="$(tr -d '\r\n' < "$TOKEN_FILE")"
    echo "Read the token from $TOKEN_FILE."
  fi
  TOKEN="$(clean "$TOKEN")"
  # Only what lies between the first { and the last } (drops rclone's
  # "Paste the following..." / "<---End paste" lines if they came along).
  TOKEN="$(printf '%s' "$TOKEN" | python3 -c 'import sys; t=sys.stdin.read(); t=t.strip(); t=t if "{" in t else "{"+t+"}"; a=t.find("{"); b=t.rfind("}"); print(t[a:b+1] if a!=-1 and b>a else "", end="")')"
  if ! printf '%s' "$TOKEN" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("refresh_token") else 1)' 2>/dev/null; then
    echo "That isn't the token line: received ${#TOKEN} usable characters (a token is about 300-600, from { to })."
    echo "Nothing written. Run this again -- or use the file way above."
    exit 1
  fi
  echo "Token received (${#TOKEN} characters)."
  rm -f "$TOKEN_FILE"
  rclone config delete gdrive >/dev/null 2>&1 || true
  rclone config create gdrive drive scope=drive root_folder_id="$FOLDER" token="$TOKEN" --non-interactive >/dev/null
  unset TOKEN
  chmod 600 "$RCONF"
  echo "rclone remote 'gdrive' written ($RCONF, root only)."
fi
echo "Folder contents now:"
rclone lsf gdrive: --max-depth 1 2>&1 | head -10 || true

echo
echo "== 3. Backup passphrase"
PASSFILE=/root/.skybre/kit-pass
if [ -s "$PASSFILE" ]; then
  read -r -p "A passphrase is already set. Keep it? [Y/n] " KP
else
  KP=n
fi
if [[ "${KP,,}" == n* ]]; then
  if [ -s "$PASSFILE" ]; then
    echo "NOTE: backups already on Drive stay readable only with the OLD passphrase."
  fi
  while true; do
    read -r -s -p "New backup passphrase (hidden, 12+ characters): " P1; echo
    read -r -s -p "Same again: " P2; echo
    [ "$P1" = "$P2" ] || { echo "They don't match."; continue; }
    [ "${#P1}" -ge 12 ] || { echo "Too short (${#P1})."; continue; }
    break
  done
  umask 077
  printf '%s' "$P1" > "$PASSFILE"
  unset P1 P2
  chmod 600 "$PASSFILE"
  echo "Saved in $PASSFILE (root only)."
  echo "*** Put this passphrase in your password manager NOW. Without it no backup can be opened. ***"
fi

cat > /root/.skybre/offsite.conf <<CONF
# deploy/offsite_backup.sh settings (package #198)
REMOTE=gdrive:
PASSFILE=$PASSFILE
PROJECT=$PROJECT
BACKUP_DIR=/home/ubuntu/backups
KEEP_KIT_DAYS=30
KEEP_DB_DAYS=7
KEEP_LOCAL_KITS=2
CONF
chmod 600 /root/.skybre/offsite.conf

echo
echo "== 4. Test"
"$SCRIPT" --test
"$SCRIPT" --db

echo
echo "== 5. Schedule (root crontab; server time zone: $(timedatectl show -p Timezone --value 2>/dev/null))"
HOURLY="20 * * * * $SCRIPT --db >/dev/null 2>&1"
NIGHTLY="40 23 * * * $SCRIPT --full >/dev/null 2>&1"
if crontab -l 2>/dev/null | grep -q 'offsite_backup.sh'; then
  echo "Root crontab already has offsite_backup.sh lines:"
  crontab -l | grep 'offsite_backup.sh'
else
  read -r -p "Add the hourly database + nightly kit lines to root's crontab? [y/N] " ADD
  if [[ "${ADD,,}" == y* ]]; then
    (crontab -l 2>/dev/null; echo "$HOURLY"; echo "$NIGHTLY") | crontab -
    echo "Added:"; echo "  $HOURLY"; echo "  $NIGHTLY"
  else
    echo "Not added. Later: sudo crontab -e and add:"; echo "  $HOURLY"; echo "  $NIGHTLY"
  fi
fi

echo
read -r -p "Make the first full rebuild kit now (takes a few minutes)? [y/N] " NOW
if [[ "${NOW,,}" == y* ]]; then
  "$SCRIPT" --full
fi
echo "Done. Log: $PROJECT/logs/offsite.log"
