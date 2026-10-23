# Partner check (21 Oct 2026, Rudolph: "Lets check Partners").
# READ ONLY -- changes nothing. Needs /tmp/splynx_audit.csv from splynx_audit.py.
#   docker compose exec -T backend python manage.py shell < partner_check.py
# For every "Partner differs" client: Splynx partner, Vexar client partner,
# account type, and each service's plan with the plan's partner and the
# service's own partner (when set) -- i.e. which network actually delivers it.
import csv
from collections import Counter

from billing.models import Service
from customers.models import Customer, Partner

rows = [r for r in csv.DictReader(open("/tmp/splynx_audit.csv", encoding="utf-8"))
        if r["Section"].endswith("Partner differs")]

print("\nVEXAR PARTNERS (clients each):")
counts = Counter(Customer.objects.exclude(status="inactive").values_list("partner__name", flat=True))
for p in Partner.objects.all():
    print(f"  {p.name:<22} {counts.get(p.name, 0):>5} clients  {'active' if p.is_active else 'SWITCHED OFF'}")
print(f"  {'(no partner)':<22} {counts.get(None, 0):>5} clients")

has_type = hasattr(Customer, "account_type")
print(f"\nPARTNER DIFFERS: {len(rows)} clients\n")
for r in rows:
    c = Customer.objects.select_related("partner").filter(customer_id=r["Vexar client"]).first()
    if c is None:
        print(f"{r['Vexar client']}: not found")
        continue
    acct = ""
    if has_type:
        t = getattr(c, "account_type", None)
        acct = str(getattr(t, "name", t) or "")
    print(f"{c.customer_id:<12} {c.full_name[:30]:<30} Splynx: {r['Splynx']:<16} Vexar: {r['Vexar']:<16} "
          f"Account type: {acct or '-'}")
    for s in c.services.exclude(status=Service.Status.TERMINATED).select_related("tariff__partner", "partner"):
        tp = s.tariff.partner.name if s.tariff_id and s.tariff.partner_id else "any partner"
        own = f" | line partner: {s.partner.name}" if s.partner_id else ""
        print(f"      {s.tariff.name if s.tariff_id else '(no plan)':<28} plan belongs to: {tp:<16}"
              f"{s.radius_username or 'no login':<32} {s.get_status_display()}{own}")
