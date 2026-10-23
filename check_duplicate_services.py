from django.db.models import Q
from customers.models import Customer
from billing.models import InvoiceItem
from audit.models import AuditEvent
cs = Customer.objects.filter(Q(full_name__icontains="voster") | Q(company_name__icontains="voster") | Q(full_name__icontains="vossie"))
print("CUSTOMERS:", [(c.pk, c.customer_id, c.full_name, c.status) for c in cs])
for c in cs:
    for s in c.services.order_by("pk"):
        print(f"\nSERVICE {s.pk}: {s.tariff} | {s.status} | start {s.start_date} | end {s.end_date} | created {s.created_at:%Y-%m-%d %H:%M} | radius {s.radius_username or '-'} | replaces {s.replaces_id}")
        for it in InvoiceItem.objects.filter(service=s).select_related("invoice"):
            inv = it.invoice
            print(f"   from {inv.number} ({inv.status}) dated {inv.date_created} line '{it.description}' {it.period_start}..{it.period_end}")
        for e in AuditEvent.objects.filter(target_type="billing.Service", target_id=str(s.pk)).order_by("created_at"):
            print(f"   audit {e.created_at:%Y-%m-%d %H:%M} {e.action} by {e.actor_label or 'system'} {e.detail[:80]}")
    print("\nTARIFF LINES ON THEIR DOCUMENTS:")
    for it in InvoiceItem.objects.filter(invoice__customer=c, item_type="tariff").select_related("invoice").order_by("invoice__date_created", "invoice_id"):
        inv = it.invoice
        print(f"   {inv.number} {inv.status} dated {inv.date_created} '{it.description}' service={it.service_id}")
    print("\nCUSTOMER HISTORY (services/invoices/leads):")
    for e in AuditEvent.objects.filter(customer=c).order_by("created_at"):
        if e.target_type in ("billing.Service", "billing.Invoice", "customers.Customer", "sales.Lead"):
            print(f"   {e.created_at:%Y-%m-%d %H:%M} {e.action:8} {e.target_type} {e.target_label[:40]} by {e.actor_label or 'system'}")
