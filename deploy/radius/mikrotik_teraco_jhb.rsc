## =========================================================================
## Skybre -- Mikrotik RouterOS OVPN server + RADIUS (FreeRADIUS) config
## Target device: the router at Teraco Johannesburg
## =========================================================================
##
## WHAT THIS DOES
## OVPN clients (customer routers/devices) connect to this Mikrotik's OVPN
## server. Instead of the Mikrotik holding a local list of usernames/
## passwords, it delegates every login to the platform's FreeRADIUS server
## (see ../README.md / RADIUS_SETUP.md). On a successful login,
## FreeRADIUS replies with:
##   - Framed-IP-Address  -> the address handed to that PPP/OVPN interface
##                            (drawn from a "Net IP Pool" on the platform)
##   - Mikrotik-Rate-Limit -> RouterOS's own vendor attribute, applied by
##                            the router automatically as an upload/download
##                            cap matching the customer's tariff -- no extra
##                            queue/simple-queue config needed on this end.
##
## IMPORTANT -- PLEASE READ BEFORE APPLYING
## This script was written and reviewed carefully against RouterOS 7.x
## syntax and the FreeRADIUS side has been fully built and tested (see
## RADIUS_SETUP.md for how). It has NOT been tested against the real
## Mikrotik hardware at Teraco -- there is no way for us to reach that
## device from here. Please:
##   1. Review every line below, especially the placeholders in ALL CAPS.
##   2. Apply it on a maintenance window / test the OVPN login before
##      relying on it for live customers.
##   3. Keep a backup of the router's existing config first:
##        /system backup save name=pre-radius-backup
##
## Replace these placeholders before running:
##   <FREERADIUS_SERVER_IP>   -- the IP of the VPS running FreeRADIUS
##   <RADIUS_SHARED_SECRET>   -- must match the secret you set for this
##                                router under Networking -> RADIUS Clients
##                                in the Skybre admin panel (and what
##                                `render_clients_conf` wrote into the
##                                FreeRADIUS server's clients.conf)
##   <OVPN_LISTEN_PORT>       -- defaults to 1194 below, change if needed
##   <SERVER_CERT_NAME>       -- name of the TLS certificate this router
##                                will present to OVPN clients (see the
##                                certificate section below if you don't
##                                already have one)
## =========================================================================

## -- 1. RADIUS client: tell this router where FreeRADIUS is -----------------
/radius
add address=<FREERADIUS_SERVER_IP> secret="<RADIUS_SHARED_SECRET>" service=ppp \
    timeout=3s

## -- 2. Turn on RADIUS for PPP (which OVPN sits on top of) ------------------
## interim-update matters more than it looks: the platform finds a customer's
## live session in the RADIUS accounting table, and a session whose last
## interim update is over 15 minutes old is treated as dead. Without interim
## updates, a long-running session goes stale and nothing can be pushed to it.
/ppp aaa
set use-radius=yes accounting=yes interim-update=5m

## -- 2b. Accept live changes from the platform (RFC 5176) -------------------
## NOT optional if you want speed changes, suspensions and restorations to
## reach a customer who is already online.
##
## RADIUS is consulted once, at login. Without this setting, a connected
## customer keeps whatever they were granted when they connected: an upgrade,
## a suspension, or a restoration reaches them only when they next happen to
## reconnect, which on a stable link can be weeks.
##
## With it, the platform sends:
##   CoA-Request        change their speed WITHOUT dropping the session
##   Disconnect-Request end the session when their address has to change
##                      (suspension moves them to the walled garden; restoring
##                       them moves them back)
##
## The packets must arrive from the same address configured in step 1, signed
## with that entry's secret -- RouterOS validates incoming dynamic
## authorization against its configured RADIUS servers. If the platform sits
## behind NAT, it is the translated source address the router sees that has to
## match. Check with `/radius incoming print`.
/radius incoming
set accept=yes port=3799

## -- 3. PPP profile used by RADIUS-authenticated OVPN sessions --------------
## Framed-IP-Address and Mikrotik-Rate-Limit from FreeRADIUS's reply apply
## automatically to sessions using this profile -- nothing else to
## configure here for that part.
/ppp profile
add name=skybre-radius-ovpn use-radius=yes

## -- 4. TLS certificate for the OVPN server ----------------------------------
## Skip this whole section if the router already has a suitable server
## certificate (check with `/certificate print`) and just set
## certificate=<SERVER_CERT_NAME> in step 5 below to the existing one.
##
## Otherwise, this generates a self-signed certificate on the router
## itself -- fine for OVPN (the customer's OVPN client is configured with
## this router's public cert/fingerprint directly, it doesn't rely on a
## public CA the way a browser would).
##
# /certificate
# add name=<SERVER_CERT_NAME> common-name=teraco-jhb-ovpn key-usage=digital-signature,key-encipherment,tls-server
# sign <SERVER_CERT_NAME>

## -- 5. OVPN server itself ----------------------------------------------------
/interface ovpn-server server
set enabled=yes port=<OVPN_LISTEN_PORT> mode=ip netmask=24 \
    certificate=<SERVER_CERT_NAME> require-client-certificate=no \
    auth=sha256,sha1 cipher=aes256-cbc,aes128-cbc \
    default-profile=skybre-radius-ovpn

## -- 6. Firewall: allow the OVPN port in ------------------------------------
## Adjust interface-list/chain names to match this router's existing
## firewall structure -- these are illustrative, not a full ruleset.
/ip firewall filter
add chain=input protocol=tcp dst-port=<OVPN_LISTEN_PORT> action=accept \
    comment="Skybre OVPN (RADIUS-authenticated)"

## =========================================================================
## VERIFICATION -- run these after applying, ideally with a spare test
## Service (Networking -> Services -> set a RADIUS username/password on a
## non-production customer first)
## =========================================================================
##
## 1. Confirm the RADIUS server is reachable and the secret is accepted:
##      /radius monitor 0
##    (should show no "invalid secret" or timeout errors once a login is
##    attempted)
##
## 2. Watch PPP logins live:
##      /ppp active print
##    -- after a successful OVPN client connection, the session should
##    appear here with the Framed-IP-Address FreeRADIUS assigned.
##
## 3. Check the router's own logs for RADIUS activity:
##      /log print where topics~"radius"
##
## 4. Confirm bandwidth shaping is actually applied:
##      /queue simple print   (RouterOS creates a dynamic queue entry per
##      PPP session when Mikrotik-Rate-Limit is present in the reply --
##      look for one named after the PPP interface, e.g. <pppoe-username>)
##
## 5. Cross-check against the platform: Networking -> Live Sessions in the
##    Skybre admin panel should show the same session (it reads the exact
##    same radacct table FreeRADIUS writes to as this router's Access-
##    Requests/Accounting-Requests land).
##
## TROUBLESHOOTING
## - "Access-Reject" for a known-good username/password: check the
##   Service's status is "Active" in Skybre (Suspended/Terminated services
##   are deliberately rejected -- see radiusauth/signals.py).
## - No Framed-IP-Address in the Access-Accept: the "Net IP Pool" this
##   platform draws from is out of free addresses -- add more addresses to
##   it under Networking -> IP Pools (category = Net IP Pool).
## - RADIUS requests time out / never reach FreeRADIUS: check this
##   router's route/firewall to the FreeRADIUS server on UDP 1812/1813,
##   and that the secret above exactly matches the NAS client entry under
##   Networking -> RADIUS Clients in the admin panel.
## =========================================================================
