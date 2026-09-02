## =========================================================================
## Skybre -- OVPN server + RADIUS on the BENCH Mikrotik
## Target: RB1100AHx4 Dude Edition, RouterOS 7.24, 102.23.159.102
## =========================================================================
##
## WHAT THIS DOES
## Makes THIS router the OVPN server. Customer OVPN clients dial in to it,
## and instead of holding a local username/password list, the router asks
## the platform's FreeRADIUS (on the VPS, 154.65.111.61) for every login.
## On success FreeRADIUS replies with:
##   Framed-IP-Address   -> the address given to that session, from a
##                          "Net IP Pool" in the platform
##   Mikrotik-Rate-Limit -> RouterOS's own attribute, applied automatically
##                          as an up/down cap matching the tariff. No queue
##                          config needed on this router.
##
## This is the production topology, on bench hardware. Note it is the
## OPPOSITE direction to the ovpn-out1 client interface you were testing:
## nothing here dials out to the VPS. Only RADIUS crosses to the VPS, on
## UDP 1812/1813.
##
## BEFORE YOU RUN THIS
##   1. Back the router up:   /system backup save name=pre-skybre-backup
##   2. Replace RADIUS_SECRET_HERE below with a secret you choose.
##   3. Add this router in the platform: Networking -> RADIUS Clients ->
##      IP 102.23.159.102, shortname bench-jhb, and THE SAME secret.
##      With the clients-sync installed that applies to FreeRADIUS by
##      itself within a few seconds -- no SSH needed.
##   4. Disable the old test interface so it stops retrying:
##        /interface disable ovpn-out1
##
## HONESTY ABOUT WHAT'S VERIFIED
## The FreeRADIUS side is built and tested. This router-side script is
## written against RouterOS 7.x syntax and reviewed, but has NOT been run
## on real hardware -- I have no way to reach your router. Apply it a
## section at a time rather than pasting the whole thing blind, and check
## the notes marked "VERIFY" where RouterOS versions differ.
## =========================================================================

## -- 1. Tell this router where FreeRADIUS is ---------------------------------
/radius
add address=154.65.111.61 secret="RADIUS_SECRET_HERE" service=ppp timeout=3s comment="Skybre FreeRADIUS (VPS)"

## -- 2. Turn on RADIUS for PPP (OVPN sits on top of PPP) ---------------------
/ppp aaa
set use-radius=yes accounting=yes interim-update=5m

## -- 3. PPP profile for RADIUS-authenticated OVPN sessions -------------------
## Framed-IP-Address and Mikrotik-Rate-Limit from the RADIUS reply apply
## automatically to sessions on this profile.
##
## VERIFY: if sessions authenticate but the interface never comes up, this
## profile may need a local-address (the router's own address on the tunnel).
## Add one from a spare /30 or your management range, e.g.:
##   /ppp profile set skybre-radius-ovpn local-address=10.250.40.1
/ppp profile
add name=skybre-radius-ovpn use-radius=yes comment="Skybre RADIUS-authenticated OVPN"

## -- 4. TLS certificate for the OVPN server ----------------------------------
## This router has no certificate yet (unlike Teraco, which reuses
## skybre-teraco.pl2_0), so we create a small local CA and a server cert.
## Self-signed is fine for OVPN: the client is pointed at this router
## directly, it doesn't rely on a public CA the way a browser does.
##
## Run these ONE AT A TIME and check `/certificate print` after each --
## signing is asynchronous and takes a few seconds on this hardware.
/certificate
add name=skybre-bench-ca common-name=skybre-bench-ca key-size=2048 days-valid=3650 \
    key-usage=key-cert-sign,crl-sign
sign skybre-bench-ca
add name=skybre-bench-ovpn common-name=102.23.159.102 key-size=2048 days-valid=3650 \
    key-usage=digital-signature,key-encipherment,tls-server
sign skybre-bench-ovpn ca=skybre-bench-ca

## -- 5. The OVPN server ------------------------------------------------------
## Port 1195 to match what we've been using (and to keep its log entries
## distinct from the Splynx dial-out on 1194).
##
## VERIFY: RouterOS 7.16+ also has a newer multi-server form
## (/interface ovpn-server servers). If the command below errors on 7.24,
## check which exists:
##   /interface ovpn-server server print
##   /interface ovpn-server servers print
## and use the corresponding path.
/interface ovpn-server server
set enabled=yes port=1195 mode=ip netmask=24 \
    certificate=skybre-bench-ovpn require-client-certificate=no \
    auth=sha256,sha1 cipher=aes256-cbc,aes128-cbc \
    default-profile=skybre-radius-ovpn

## -- 6. Firewall -------------------------------------------------------------
## Deliberately NOT adding a rule. Verified on this router: both
## /ip firewall filter and /ip firewall nat are EMPTY, and RouterOS's
## default for an empty chain is accept, so port 1195 is already reachable.
## Adding a lone accept rule to an empty chain changes nothing, and
## firewalling this router is separate work that shouldn't happen as a side
## effect of this script. Same call as was made for Teraco.

## =========================================================================
## VERIFICATION, in order
## =========================================================================
##
## 1. Is the OVPN server actually listening?
##      /interface ovpn-server server print
##    enabled should be yes and the certificate should be set.
##
## 2. Can RADIUS be reached at all? Watch this while attempting a login:
##      /radius monitor 0
##    Look for requests going out and replies coming back. "timeout"
##    means the VPS isn't answering -- which is the same UDP question we
##    hit on 1195, so check it here early. On the VPS, confirm requests
##    arrive at all with:
##      sudo tcpdump -ni ens3 'udp port 1812'
##
## 3. Create a test Service in the platform with a RADIUS username and
##    password (Networking -> Services), then dial in with any OVPN client
##    using those credentials, port 1195, cipher aes256-cbc, auth sha256.
##
## 4. Watch the session appear:
##      /ppp active print
##    It should show the Framed-IP-Address FreeRADIUS assigned.
##
## 5. Confirm the rate limit landed:
##      /queue simple print
##    RouterOS creates a dynamic queue per session when Mikrotik-Rate-Limit
##    is in the reply.
##
## 6. Cross-check in the platform: Networking -> Live Sessions reads the
##    same radacct table FreeRADIUS writes, so the session should appear
##    there too.
##
## TROUBLESHOOTING
## - Access-Reject for good credentials: check the Service's status is
##   Active in the platform. Suspended/Terminated are rejected on purpose.
## - No Framed-IP-Address in the Accept: the Net IP Pool is out of free
##   addresses. Add more under Networking -> IP Pools (Net IP Pool).
## - RADIUS times out: the secret here must match the RADIUS Clients entry
##   exactly, AND the VPS must be seeing the packets. Check both.
## - "invalid secret" in /radius monitor: the two secrets differ. Re-save
##   the RADIUS Client in the frontend; it re-applies automatically.
## =========================================================================
