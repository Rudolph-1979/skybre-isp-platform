#!/usr/bin/env bash
# Rebuild Vexar from a rebuild kit -- package #198, 19 Oct 2026.
#
# On a FRESH Ubuntu 24.04 server, as a user with sudo:
#     sudo bash restore.sh --test skybre-kit-YYYY-MM-DD-HHMM.tar.gpg
#     sudo bash restore.sh --live skybre-kit-YYYY-MM-DD-HHMM.tar.gpg
#
# --test  practice run on a spare server: everything is restored and
#         started so you can log in and check it, but NOTHING goes out --
#         no crontab (no billing, emails, statements, router polling,
#         Conecto/Webfleet syncs), outgoing email switched to "print only",
#         Conecto/Webfleet keys switched off, no OpenVPN, no firewall change.
#         Throw the server away afterwards.
# --live  the real rebuild after Vexar is lost: also starts OpenVPN, the
#         RADIUS clients sync, nginx/HTTPS, the crontabs and (if it lets SSH
#         in) the firewall. Do it only when the old server is really gone --
#         two live Vexars would both bill and both email customers.
#
# Get the kit: Google Drive -> "Vexar backups" -> kits -> download the newest
# skybre-kit-*.tar.gpg, then copy it and this script to the new server, e.g.
#     scp -o IdentitiesOnly=yes -i C:\keys\skybre.pem restore.sh skybre-kit-....tar.gpg ubuntu@NEW-IP:~/
# You need the backup passphrase (password manager). Order follows REBUILD.md.
set -euo pipefail

MODE="${1:-}"; KIT="${2:-}"; FORCE="${3:-}"
case "$MODE" in --test|--live) ;; *) sed -n '2,24p' "$0"; exit 2 ;; esac
[ -f "$KIT" ] || { echo "Kit file not found: $KIT"; exit 1; }
[ "$(id -u)" = 0 ] || { echo "Run with sudo."; exit 1; }
KIT="$(readlink -f "$KIT")"
APPUSER=ubuntu
HOME_DIR=/home/$APPUSER
PROJECT=$HOME_DIR/isp-platform
WORK=/root/restore-work
LOGF=/root/restore-$(date +%Y%m%d-%H%M).log
exec > >(tee -a "$LOGF") 2>&1

say() { echo; echo "=== $*"; }
ask() { local a; read -r -p "$1 [y/N] " a; [[ "${a,,}" == y* ]]; }
warn() { echo "!!! $*"; }

. /etc/os-release
[ "${VERSION_ID:-}" = "24.04" ] || warn "This is $PRETTY_NAME, not Ubuntu 24.04 -- carrying on, but watch for errors."

say "1/9 Open the kit"
command -v gpg >/dev/null || { apt-get update -qq; apt-get install -y -qq gnupg; }
read -r -s -p "Backup passphrase (hidden): " PASS; echo
rm -rf "$WORK"; mkdir -p "$WORK"; chmod 700 "$WORK"
if ! gpg --batch --quiet --pinentry-mode loopback --passphrase-fd 3 -d "$KIT" 3<<<"$PASS" | tar xf - -C "$WORK"; then
  echo "Could not open the kit -- wrong passphrase or damaged file."; exit 1
fi
unset PASS
K="$WORK/kit"
(cd "$K" && sha256sum --quiet -c MANIFEST.sha256) || { echo "Kit files don't match their checksums -- damaged. Stopping."; exit 1; }
echo "Kit checks out."
inv() { sed -n "s/^$1[= ]//p" "$K/inventory.txt" | head -1; }
echo "Made:       $(inv KIT_CREATED) on $(inv SOURCE_HOST) ($(inv SOURCE_IPS))"
echo "Counts:     $(inv COUNTS)"
echo "Time zone:  $(inv TIMEZONE)"
echo "Mode:       $MODE"
if [ "$MODE" = --live ]; then
  echo
  echo "LIVE restore: this server will take over billing, emails, RADIUS and the routers' VPN."
  echo "Only continue if the old Vexar server is off or gone."
fi
read -r -p "Type RESTORE to continue: " OK
[ "$OK" = RESTORE ] || { echo "Stopped. Nothing changed on this server."; exit 1; }

say "2/9 Packages"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx certbot python3-certbot-nginx freeradius freeradius-postgresql \
  freeradius-utils openvpn easy-rsa ufw unzip iputils-ping gnupg rclone curl ca-certificates
if ! docker compose version >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2
fi
systemctl enable --now docker
id "$APPUSER" >/dev/null 2>&1 || { useradd -m -s /bin/bash "$APPUSER"; usermod -aG sudo "$APPUSER"; }
usermod -aG docker "$APPUSER"
TZ_OLD="$(inv TIMEZONE)"
[ -n "$TZ_OLD" ] && timedatectl set-timezone "$TZ_OLD" && echo "Time zone set to $TZ_OLD (cron times depend on it)."
# The packages' stock services stay off until their config is back.
systemctl stop freeradius 2>/dev/null || true

say "3/9 Code, .env and compose files"
if [ -e "$PROJECT" ] && [ -n "$(ls -A "$PROJECT" 2>/dev/null)" ]; then
  if [ "$FORCE" = --force ]; then
    mv "$PROJECT" "$PROJECT.before-restore-$(date +%Y%m%d%H%M)"
  else
    echo "$PROJECT already exists and isn't empty. Add --force as the 3rd argument to move it aside."; exit 1
  fi
fi
tar xzf "$K/app/isp-platform.tar.gz" -C "$HOME_DIR"
mkdir -p "$PROJECT/logs" "$HOME_DIR/backups/db/hourly" "$HOME_DIR/backups/db/daily" "$HOME_DIR/backups/media"
chown -R "$APPUSER:$APPUSER" "$PROJECT" "$HOME_DIR/backups"
chmod 750 "$HOME_DIR/backups" "$HOME_DIR/backups/db"
[ -f "$PROJECT/backend/.env" ] || { echo "backend/.env is missing from the kit -- can't start Vexar."; exit 1; }
if [ "$MODE" = --test ]; then
  # Nothing may reach customers, Conecto or Webfleet from a practice server.
  sed -i -E 's/^(EMAIL_HOST|CONECTO_API_KEY|WEBFLEET_API_KEY|WEBFLEET_PASSWORD)=/# disabled by restore.sh --test: &/' "$PROJECT/backend/.env"
  # Reached through an SSH tunnel on http://localhost:8080 (see the end).
  sed -i -E -e 's/^(ALLOWED_HOSTS=.*)$/\1,localhost,127.0.0.1/' \
            -e 's/^(SECURE_SSL_REDIRECT|SESSION_COOKIE_SECURE|CSRF_COOKIE_SECURE)=.*/\1=False/' "$PROJECT/backend/.env"
  # The practice copy's pages must call ITS OWN backend, never the live one.
  if [ -f "$PROJECT/frontend/.env" ]; then
    sed -i -E 's#^VITE_API_BASE_URL=.*#VITE_API_BASE_URL=/api#' "$PROJECT/frontend/.env"
  fi
  echo "Test mode: email, Conecto and Webfleet keys off; reachable on http://localhost:8080 through a tunnel;"
  echo "           the pages talk to this server's own /api only."
fi

say "4/9 Docker: build and start the database"
cd "$PROJECT"
sudo -u "$APPUSER" docker compose build
sudo -u "$APPUSER" docker compose up -d db
for i in $(seq 1 60); do
  sudo -u "$APPUSER" docker compose exec -T db sh -c 'pg_isready -U "${POSTGRES_USER:-postgres}"' >/dev/null 2>&1 && break
  sleep 2
done

say "5/9 Restore the database and uploads"
sudo -u "$APPUSER" docker compose exec -T db sh -c \
  'pg_restore -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-${POSTGRES_USER:-postgres}}" --clean --if-exists --no-owner' \
  < "$K/db/isp.dump" || warn "pg_restore reported problems (often harmless 'does not exist' notes) -- the counts below decide."
sudo -u "$APPUSER" docker compose up -d
for i in $(seq 1 90); do
  sudo -u "$APPUSER" docker compose exec -T backend python -c "print()" >/dev/null 2>&1 && break
  sleep 2
done
sleep 5
sudo -u "$APPUSER" docker compose restart frontend
if [ -f "$K/media/media.tar.gz" ]; then
  sudo -u "$APPUSER" docker compose exec -T backend sh -c 'cd /app && tar xzf -' < "$K/media/media.tar.gz" && echo "Uploads restored."
fi
if [ "$MODE" = --test ]; then
  sudo -u "$APPUSER" docker compose exec -T backend python manage.py shell -c \
    "from notifications.models import EmailSettings; r=EmailSettings.load(); r.smtp_host=''; r.save(); print('Test mode: outgoing email now prints instead of sending.')"
fi
NOW_COUNTS=$(sudo -u "$APPUSER" docker compose exec -T backend python manage.py shell -c \
  "from customers.models import Customer; from billing.models import Service, Invoice, Payment; print(f'customers={Customer.objects.count()} services={Service.objects.count()} invoices={Invoice.objects.count()} payments={Payment.objects.count()}')" 2>/dev/null | tail -1)
echo "Counts in the kit: $(inv COUNTS)"
echo "Counts restored:   $NOW_COUNTS"
[ "$NOW_COUNTS" = "$(inv COUNTS)" ] && echo "MATCH." || warn "Counts differ -- check before going further."
PENDING=$(sudo -u "$APPUSER" docker compose exec -T backend python manage.py showmigrations 2>/dev/null | grep -c '\[ \]' || true)
[ "$PENDING" = 0 ] && echo "All migrations applied." || warn "$PENDING migration(s) not applied."

say "6/9 Host configuration (FreeRADIUS, OpenVPN, nginx, certificates, units, firewall rules)"
# The packages just installed their stock config; Skybre's replaces it whole
# (a stock module or site left next to it would change behaviour).
for d in etc/freeradius/3.0 etc/nginx/sites-enabled; do
  if grep -qx "$d" "$K/host/paths.txt" && [ -e "/$d" ]; then
    mv "/$d" "/$d.package-default-$(date +%Y%m%d%H%M)"
  fi
done
# By name, as root: owners/permissions come back as they were (freerad etc.).
tar xzf "$K/host/etc.tar.gz" -C / --same-owner
systemctl daemon-reload
if sudo -u freerad freeradius -C >/dev/null 2>&1; then
  echo "FreeRADIUS config checks out (as freerad)."
  systemctl enable freeradius >/dev/null 2>&1 || true
  systemctl restart freeradius && echo "FreeRADIUS started."
else
  warn "FreeRADIUS config does not check -- run: sudo -u freerad freeradius -CX | tail -20"
fi
if nginx -t 2>/dev/null; then systemctl reload nginx || systemctl restart nginx; echo "nginx OK."; else warn "nginx -t failed -- check /etc/nginx/sites-enabled"; fi

say "7/9 Services that talk to the outside world"
VPN_UNITS=""
for c in /etc/openvpn/server/*.conf; do [ -e "$c" ] && VPN_UNITS="$VPN_UNITS openvpn-server@$(basename "$c" .conf)"; done
if [ "$MODE" = --live ]; then
  for u in $VPN_UNITS; do systemctl enable --now "$u" && echo "$u started."; done
  for u in /etc/systemd/system/skybre-*.path; do [ -e "$u" ] && systemctl enable --now "$(basename "$u")" && echo "$(basename "$u") on."; done
  sudo -u "$APPUSER" docker compose exec -T backend python manage.py render_clients_conf --spool 2>/dev/null \
    && echo "RADIUS clients list re-sent to FreeRADIUS." || warn "render_clients_conf failed -- run it by hand (REBUILD.md 7.1)."
else
  echo "Test mode: OpenVPN ($VPN_UNITS) and the RADIUS clients sync left OFF."
fi

say "8/9 Crontabs"
if [ "$MODE" = --live ]; then
  [ -s "$K/host/crontab-ubuntu.txt" ] && crontab -u "$APPUSER" "$K/host/crontab-ubuntu.txt" && echo "ubuntu crontab restored ($(grep -cv '^\s*#' "$K/host/crontab-ubuntu.txt") lines)."
  if [ -s "$K/host/crontab-root.txt" ]; then
    # The off-site lines need deploy/setup_offsite.sh first (Drive login + passphrase).
    grep -v 'offsite_backup.sh' "$K/host/crontab-root.txt" | crontab -
    echo "root crontab restored (off-site backup lines left out until setup_offsite.sh is run)."
  fi
else
  echo "Test mode: NO crontab -- no billing, emails, polling or syncs run on this server."
fi

say "9/9 Firewall"
if [ "$MODE" = --live ]; then
  if grep -qE -- '--dport 22( |$)|dport 22 ' /etc/ufw/user.rules 2>/dev/null || grep -qE '^### tuple ### allow tcp 22 ' /etc/ufw/user.rules 2>/dev/null; then
    if ask "Turn the firewall (ufw) on with the old server's rules (SSH 22 is allowed in them)?"; then
      ufw --force enable && ufw status numbered
    fi
  else
    warn "The restored firewall rules don't show SSH (22) allowed -- NOT turning ufw on. Check REBUILD.md 7.4."
  fi
else
  echo "Test mode: firewall left as it is."
fi

rm -rf "$WORK"
say "Done ($MODE). Log: $LOGF"
if [ "$MODE" = --test ]; then
cat <<TXT
Check it from your PC with a tunnel (the site only listens on 127.0.0.1:8080 here):
    ssh -o IdentitiesOnly=yes -i C:\\keys\\skybre.pem -L 8080:127.0.0.1:8080 ubuntu@THIS-SERVER-IP
then open http://localhost:8080 and log in as usual. Look, don't change: this is a copy.
When done, delete this server.
TXT
else
cat <<TXT
Still to do by hand (REBUILD.md):
  - DNS: point portal.skybre.co.za to this server's IP if it changed, then: sudo certbot --nginx -d portal.skybre.co.za
  - Routers: if the IP changed, the RADIUS server/secret and the core router's ovpn-client "connect to" (REBUILD.md 7.5)
  - Xneelo's cloud firewall: same ports as before
  - Billing: Finance -> Recurring Billing -> preview and approve before 15:00
  - Off-site backups: cd ~/isp-platform && sudo bash deploy/setup_offsite.sh
  - Verification checklist: REBUILD.md section 10
TXT
fi
