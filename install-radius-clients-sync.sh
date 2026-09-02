#!/usr/bin/env bash
#
# Skybre — make "change the RADIUS client IP in the frontend" actually reach
# FreeRADIUS, with no SSH afterwards.
#
# How it fits together:
#   1. Saving a RADIUS client in the admin panel writes a rendered
#      clients.conf to /var/lib/skybre/radius/clients.conf (bind-mounted
#      into the backend container as /srv/radius). See
#      backend/radiusauth/clients_conf.py.
#   2. This script installs a systemd PATH unit watching that file, plus a
#      one-shot service that VALIDATES the config and only then installs it
#      into /etc/freeradius/3.0/clients.conf.d/ and reloads FreeRADIUS.
#
# The validation is the whole point. Reloading FreeRADIUS with a broken
# clients.conf drops authentication for every customer on the network, so a
# bad render must never reach the live config. If validation fails, the
# previous good config stays in place and the failure is logged.
#
# Safe to re-run.
#
set -euo pipefail

SPOOL_DIR="/var/lib/skybre/radius"
SPOOL_FILE="${SPOOL_DIR}/clients.conf"
RADDB="/etc/freeradius/3.0"
CLIENTS_D="${RADDB}/clients.conf.d"
TARGET="${CLIENTS_D}/skybre_clients.conf"
APPLY="/usr/local/sbin/skybre-apply-radius-clients"

log()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m    ! %s\033[0m\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo." >&2; exit 1; }

log "Pre-flight"
if [ ! -d "${RADDB}" ]; then
    warn "${RADDB} does not exist — is FreeRADIUS installed on this host?"
    exit 1
fi
echo "    FreeRADIUS config dir found at ${RADDB}"

if ! systemctl list-unit-files 'freeradius*' --no-pager 2>/dev/null | grep -q freeradius; then
    warn "No freeradius systemd unit found. Install/enable FreeRADIUS first."
    exit 1
fi
echo "    freeradius unit present"

log "Creating the spool directory"
# 0700 root-only: this file holds RADIUS shared secrets in plaintext, same
# as clients.conf itself. The backend container runs as root, so it can
# still write here through the bind mount.
install -d -m 700 -o root -g root "${SPOOL_DIR}"
echo "    ${SPOOL_DIR} (0700 root:root)"

log "Ensuring FreeRADIUS includes ${CLIENTS_D}"
install -d -m 750 "${CLIENTS_D}"
if grep -rqs 'clients\.conf\.d' "${RADDB}/radiusd.conf" "${RADDB}/clients.conf"; then
    echo "    include already present"
else
    # The DIRECTORY form (trailing slash), not a *.conf glob. FreeRADIUS 3.2
    # does not expand globs in $INCLUDE -- it treats "clients.conf.d/*.conf"
    # as a literal filename and fails the ENTIRE config parse with "Unable to
    # open file", which would stop FreeRADIUS from starting and take down
    # authentication for every customer. Verified against FreeRADIUS 3.2.5:
    # the glob form fails even when a matching file exists, while the
    # directory form validates cleanly both empty and populated.
    printf '\n# Added by install-radius-clients-sync.sh — Skybre-managed NAS clients\n$INCLUDE clients.conf.d/\n' \
        >> "${RADDB}/clients.conf"
    echo "    added \$INCLUDE clients.conf.d/ to ${RADDB}/clients.conf"

    # Validate immediately, and back the change out if it broke anything --
    # never leave this host with a FreeRADIUS config that won't parse.
    if ! freeradius -CX -d "${RADDB}" >/tmp/skybre-radius-include-check.log 2>&1; then
        warn "adding the include broke the FreeRADIUS config — reverting it"
        sed -i '/^\$INCLUDE clients\.conf\.d\/$/d; /^# Added by install-radius-clients-sync\.sh/d' "${RADDB}/clients.conf"
        warn "see /tmp/skybre-radius-include-check.log"
        exit 1
    fi
    echo "    config still validates after adding the include"
fi

log "Installing the apply script at ${APPLY}"
cat > "${APPLY}" <<APPLYEOF
#!/usr/bin/env bash
#
# Installs the admin-panel-generated clients.conf into FreeRADIUS, but only
# if FreeRADIUS itself says the resulting config is valid. Run by
# skybre-radius-clients.service, triggered by skybre-radius-clients.path
# when the spool file changes.
#
set -euo pipefail

SPOOL_FILE="${SPOOL_FILE}"
TARGET="${TARGET}"
RADDB="${RADDB}"

logger -t skybre-radius-clients "spool change detected"

[ -f "\${SPOOL_FILE}" ] || { logger -t skybre-radius-clients "spool file missing, nothing to do"; exit 0; }

# Unchanged content: do nothing rather than reload FreeRADIUS pointlessly.
if [ -f "\${TARGET}" ] && cmp -s "\${SPOOL_FILE}" "\${TARGET}"; then
    logger -t skybre-radius-clients "no change in content, skipping reload"
    exit 0
fi

# Stage the candidate where FreeRADIUS's own config check will read it, so
# we validate the REAL resulting config rather than the file in isolation.
BACKUP="\$(mktemp /tmp/skybre-clients-backup.XXXXXX)"
if [ -f "\${TARGET}" ]; then cp -p "\${TARGET}" "\${BACKUP}"; else : > "\${BACKUP}"; fi

install -m 640 -o root -g freerad "\${SPOOL_FILE}" "\${TARGET}" 2>/dev/null \
  || install -m 640 "\${SPOOL_FILE}" "\${TARGET}"

# freeradius -CX parses the whole config and exits non-zero if it's broken.
if freeradius -CX -d "\${RADDB}" >/tmp/skybre-radius-check.log 2>&1; then
    if systemctl reload freeradius >/dev/null 2>&1 || systemctl restart freeradius >/dev/null 2>&1; then
        logger -t skybre-radius-clients "config valid, installed and reloaded FreeRADIUS"
        rm -f "\${BACKUP}"
        exit 0
    fi
    logger -t skybre-radius-clients "ERROR: config valid but FreeRADIUS would not reload; rolling back"
else
    logger -t skybre-radius-clients "ERROR: generated clients.conf FAILED validation; rolling back (see /tmp/skybre-radius-check.log)"
fi

# Roll back to whatever was working before, and make sure the daemon is
# running on that known-good config.
if [ -s "\${BACKUP}" ]; then
    cp -p "\${BACKUP}" "\${TARGET}"
else
    rm -f "\${TARGET}"
fi
rm -f "\${BACKUP}"
systemctl reload freeradius >/dev/null 2>&1 || systemctl restart freeradius >/dev/null 2>&1 || true
logger -t skybre-radius-clients "rolled back to the previous clients.conf"
exit 1
APPLYEOF
chmod 750 "${APPLY}"
echo "    installed"

log "Installing systemd units"
cat > /etc/systemd/system/skybre-radius-clients.service <<EOF
[Unit]
Description=Install Skybre admin-panel RADIUS clients into FreeRADIUS and reload
After=freeradius.service

[Service]
Type=oneshot
# Small settle delay so a burst of saves in the admin panel results in one
# reload rather than several.
ExecStartPre=/bin/sleep 2
ExecStart=${APPLY}
EOF

cat > /etc/systemd/system/skybre-radius-clients.path <<EOF
[Unit]
Description=Watch the Skybre RADIUS clients spool for changes

[Path]
PathChanged=${SPOOL_FILE}
Unit=skybre-radius-clients.service

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now skybre-radius-clients.path >/dev/null
echo "    skybre-radius-clients.path enabled and running"

log "Applying whatever is in the spool right now"
if [ -f "${SPOOL_FILE}" ]; then
    if "${APPLY}"; then
        echo "    applied and FreeRADIUS reloaded"
    else
        warn "apply failed — see: journalctl -t skybre-radius-clients -n 20"
    fi
else
    echo "    no spool file yet (it appears the first time you save a RADIUS client)"
fi

cat <<EOF

$(printf '\033[1m')Done.$(printf '\033[0m')

From now on, editing a RADIUS client under Networking -> RADIUS Clients --
including changing its IP -- reaches FreeRADIUS on its own, within a few
seconds. No SSH, no copying files, no manual reload.

Watch it happen:

    journalctl -t skybre-radius-clients -f

Then change a RADIUS client's IP in the frontend and save. Expect:
    "spool change detected" then "config valid, installed and reloaded FreeRADIUS"

Check what FreeRADIUS is actually trusting right now:

    sudo cat ${TARGET}

If a generated config is ever invalid, it is REJECTED and the previous one
stays live -- FreeRADIUS is never reloaded with a broken clients.conf. The
reason lands in the journal and in /tmp/skybre-radius-check.log.

To remove all of this:

    sudo systemctl disable --now skybre-radius-clients.path
    sudo rm -f /etc/systemd/system/skybre-radius-clients.{path,service} ${APPLY}
    sudo systemctl daemon-reload
    # ${TARGET} stays in place; delete it if you want those clients gone too.

EOF
