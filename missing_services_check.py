# Missing services check (21 Oct 2026, Rudolph: "show me missing services").
# READ ONLY -- changes nothing. Needs /tmp/splynx_audit.csv from splynx_audit.py.
#   docker compose exec -T backend python manage.py shell < missing_services_check.py
# For every Splynx line with no Vexar service: has that login ever connected
# through Vexar's RADIUS (last session, online now?), and what the client has
# in Vexar today. A login online now with no Vexar service = live, unbilled.
import csv
import re

from django.utils import timezone

from billing.models import Service
from customers.models import Customer
from radiusauth.models import RadAcct

rows = [r for r in csv.DictReader(open("/tmp/splynx_audit.csv", encoding="utf-8"))
        if r["Section"].endswith("Service missing in Vexar")]


def when(dt):
    return timezone.localtime(dt).strftime("%d/%m/%Y %H:%M") if dt else "-"


print(f"\nMISSING SERVICES: {len(rows)} Splynx lines with no Vexar service\n")
last_client = None
online_n = seen_n = 0
for r in rows:
    plan, _, login = r["Splynx"].partition(" · ")
    login = login.strip()
    if r["Vexar client"] != last_client:
        last_client = r["Vexar client"]
        c = Customer.objects.filter(customer_id=last_client).first()
        have = []
        if c:
            have = [f"{s.tariff.name if s.tariff_id else '?'} ({s.radius_username or 'no login'}, {s.get_status_display()})"
                    for s in c.services.exclude(status=Service.Status.TERMINATED).select_related("tariff")]
        print(f"{last_client:<12} {r['Name'][:34]:<34} Vexar has {len(have)} service(s)"
              + (": " + "; ".join(have[:4]) + (" ..." if len(have) > 4 else "") if have else ""))
    sess = None
    if login and login != "no login":
        sess = RadAcct.objects.filter(username__iexact=login).order_by("-acctstarttime").first()
    if sess is None:
        live = "never connected through Vexar"
    elif sess.acctstoptime is None:
        live = f"ONLINE NOW (since {when(sess.acctstarttime)})"
        online_n += 1
    else:
        live = f"last online {when(sess.acctstoptime)}"
        seen_n += 1
    print(f"     {plan[:22]:<22} {login[:34]:<34} {live}")

print(f"\n{online_n} online now (live, not billed in Vexar), {seen_n} seen before but offline, "
      f"{len(rows) - online_n - seen_n} never connected through Vexar.")
