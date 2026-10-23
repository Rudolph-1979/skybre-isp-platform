#!/bin/bash
# FreeRADIUS: accept a PPPoE username with spaces before/after it (22 Oct 2026,
# Rudolph: "spaces behind client's router pppoe" -> "Option 3 please").
#
# Today the standard filter_username check REJECTS any username containing a
# space, before Vexar's radcheck table is even looked at. After this:
#   * "smf_hnm_name " (or " smf_hnm_name") from a router is looked up -- and
#     accounted -- as "smf_hnm_name", so it matches the Vexar login.
#   * User-Name itself is NOT changed: MS-CHAPv2 checks the password against
#     the exact name the router sent, so rewriting it would break that.
#     The trimmed name is kept in control:Tmp-String-9 and the SQL lookup and
#     accounting use it (sql_user_name in queries.conf).
#   * An "&" in a name (smf_hnm_trailor_&_tool) now works. FreeRADIUS's SQL
#     module escapes characters outside its "safe" list, so "&" was looked up
#     as "=26" and never found. "&" is added to safe_characters (it is
#     harmless inside an SQL string; quotes stay escaped).
#   * A space INSIDE a name ("jan smit") is no longer rejected either: it is
#     looked up as it is, so it matches a Vexar login with the same space
#     (Rudolph: "we want to allow a router that has a space in Vexar").
#
# Run on the server (host, not a container):  sudo bash freeradius_trim_username.sh
# Backs up every file it touches (*.skybre-<time>), checks the config with
# "freeradius -XC" before restarting, and puts everything back if the check
# or the restart fails. Safe to run twice.
set -u
RD=/etc/freeradius/3.0
SITE=$RD/sites-available/default
QUERIES=$RD/mods-config/sql/main/postgresql/queries.conf
SQLMOD=$(readlink -f $RD/mods-enabled/sql)
SAFE='@abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_: /&'
POLICY=$RD/policy.d/skybre_username
STAMP=$(date +%Y%m%d-%H%M%S)

[ "$(id -u)" = "0" ] || { echo "Run with sudo."; exit 1; }
for f in "$SITE" "$QUERIES" "$SQLMOD"; do [ -f "$f" ] || { echo "Missing $f -- stopping, nothing changed."; exit 1; }; done

if [ -f "$POLICY" ] && grep -q "skybre_filter_username" "$SITE" && grep -q "Tmp-String-9" "$QUERIES" && grep -q "^\s*safe_characters = .*&" "$SQLMOD"; then
    echo "Already applied -- nothing to do."
    exit 0
fi

cp -p "$SITE" "$SITE.skybre-$STAMP"
cp -p "$QUERIES" "$QUERIES.skybre-$STAMP"
cp -p "$SQLMOD" "$SQLMOD.skybre-$STAMP"
[ -f "$POLICY" ] && cp -p "$POLICY" "$POLICY.skybre-$STAMP"
echo "Backups: *.skybre-$STAMP"

restore() {
    echo "!! $1 -- putting the old config back."
    cp -p "$SITE.skybre-$STAMP" "$SITE"
    cp -p "$QUERIES.skybre-$STAMP" "$QUERIES"
    cp -p "$SQLMOD.skybre-$STAMP" "$SQLMOD"
    if [ -f "$POLICY.skybre-$STAMP" ]; then cp -p "$POLICY.skybre-$STAMP" "$POLICY"; else rm -f "$POLICY"; fi
    systemctl restart freeradius
    sleep 2
    systemctl is-active --quiet freeradius && echo "FreeRADIUS is running on the old config." || echo "!! FreeRADIUS is NOT running -- check: journalctl -u freeradius -n 50"
    exit 1
}

# 1. The policy.
cat > "$POLICY" <<'EOF'
# Skybre (22 Oct 2026) -- see freeradius_trim_username.sh.
# A router that sends its PPPoE username with spaces before/after it is looked
# up (and accounted) by the trimmed name. User-Name itself is left as sent:
# MS-CHAPv2 checks the password against the name the router used.

skybre_trim_username {
	if (&User-Name && (&User-Name =~ /^[[:space:]]*(.*[^[:space:]])[[:space:]]*$/)) {
		if ("%{1}" != "%{User-Name}") {
			update control {
				&Tmp-String-9 := "%{1}"
			}
		}
	}
}

skybre_filter_username {
	# Replaces the standard filter_username, which rejects ANY username
	# containing a space (Rudolph: a router with a space must still be let in).
	# Spaces before/after: trimmed above. Spaces inside: allowed, looked up as
	# they are. The realm (@) checks of filter_username are kept.
	skybre_trim_username
	if (&User-Name =~ /@[^@]*@/ ) {
		update request {
			&Module-Failure-Message += 'Rejected: Multiple @ in User-Name'
		}
		reject
	}
	if (&User-Name =~ /\.\./ ) {
		update request {
			&Module-Failure-Message += 'Rejected: User-Name contains multiple ..s'
		}
		reject
	}
}
EOF
chown freerad:freerad "$POLICY"
chmod 640 "$POLICY"

# 2. authorize: filter_username -> skybre_filter_username; preacct: trim too.
python3 - "$SITE" <<'PY' || restore "Could not edit sites-available/default"
import re, sys
p = sys.argv[1]
lines = open(p).read().split("\n")
section = None
done_auth = done_acct = False
out = []
for line in lines:
    m = re.match(r"^(\w[\w-]*)\s*\{", line)
    if m:
        section = m.group(1)
    if section == "authorize" and not done_auth and re.match(r"^\s*filter_username\s*$", line):
        out.append(line.replace("filter_username", "skybre_filter_username"))
        done_auth = True
        continue
    out.append(line)
    if section == "preacct" and not done_acct and re.match(r"^\s*preprocess\s*$", line):
        out.append(line.replace("preprocess", "skybre_trim_username"))
        done_acct = True
if not (done_auth and done_acct):
    sys.exit("authorize filter_username found: %s, preacct preprocess found: %s" % (done_auth, done_acct))
open(p, "w").write("\n".join(out))
PY

# 3. SQL lookup + accounting by the trimmed name when there is one.
sed -i 's|^sql_user_name = "%{User-Name}"$|sql_user_name = "%{%{control:Tmp-String-9}:-%{User-Name}}"|' "$QUERIES"
grep -q 'Tmp-String-9' "$QUERIES" || restore "sql_user_name line not found in queries.conf"

# 3b. "&" allowed in SQL lookups (else it is escaped to "=26" and not found).
python3 - "$SQLMOD" "$SAFE" <<'PY' || restore "Could not set safe_characters in the sql module"
import re, sys
p, safe = sys.argv[1], sys.argv[2]
lines = open(p).read().split("\n")
out, done = [], False
for line in lines:
    if re.match(r"^\s*safe_characters\s*=", line):
        continue            # replaced by ours below
    out.append(line)
    if not done and re.match(r"^\s*driver\s*=", line):
        out.append('\tsafe_characters = "%s"' % safe)
        done = True
if not done:
    sys.exit("driver line not found")
open(p, "w").write("\n".join(out))
PY

# 4. Check, then restart.
if ! freeradius -XC > /tmp/freeradius-check.log 2>&1; then
    tail -20 /tmp/freeradius-check.log
    restore "Config check failed"
fi
systemctl restart freeradius
sleep 3
systemctl is-active --quiet freeradius || restore "FreeRADIUS did not start"

echo
echo "DONE. FreeRADIUS restarted with the space-trimming rule."
echo "Changed: policy.d/skybre_username (new), sites-available/default (authorize + preacct), queries.conf (sql_user_name), mods-available/sql (safe_characters)."
echo "To undo: sudo cp -p $SITE.skybre-$STAMP $SITE; sudo cp -p $QUERIES.skybre-$STAMP $QUERIES; sudo cp -p $SQLMOD.skybre-$STAMP $SQLMOD; sudo rm $POLICY; sudo systemctl restart freeradius"
