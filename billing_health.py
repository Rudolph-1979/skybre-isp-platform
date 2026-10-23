# READ-ONLY billing health check. Writes nothing.
from collections import defaultdict
from decimal import Decimal
from django.db.models import Sum
from billing.models import Invoice, Payment, PaymentAllocation, CreditRequest
from billing.allocation import UNALLOCATABLE_STATUSES
from billing.statements import IMPORT_NOTE_PREFIX
from customers.models import Customer

Z = Decimal("0")
OWED = (Invoice.Status.UNPAID, Invoice.Status.OVERDUE)
alloc_by_pay = defaultdict(Decimal); alloc_by_inv = defaultdict(Decimal)
for a in PaymentAllocation.objects.values("payment_id", "invoice_id", "amount"):
    alloc_by_pay[a["payment_id"]] += a["amount"]
    if a["invoice_id"]: alloc_by_inv[a["invoice_id"]] += a["amount"]
direct_by_inv = defaultdict(Decimal)
for p in Payment.objects.exclude(invoice=None).values("invoice_id", "amount"):
    direct_by_inv[p["invoice_id"]] += p["amount"]
credit_by_inv = defaultdict(Decimal)
has_link = any(f.name == "invoice" for f in CreditRequest._meta.get_fields())
if has_link:
    for c in CreditRequest.objects.filter(status="approved").exclude(invoice=None).values("invoice_id", "amount"):
        credit_by_inv[c["invoice_id"]] += c["amount"]

def imported(note): return (note or "").startswith(IMPORT_NOTE_PREFIX)

open_by_cust = defaultdict(list)
inv = list(Invoice.objects.exclude(status__in=UNALLOCATABLE_STATUSES).filter(total__gt=0)
           .values("id", "number", "customer_id", "status", "total", "paid_amount", "date_due"))
for i in inv:
    if i["paid_amount"] < i["total"]: open_by_cust[i["customer_id"]].append(i)

# 1. unapplied money on payments
unapplied_with_debt = []; unapplied_no_debt = Z; n_no_debt = 0
for p in Payment.objects.filter(invoice=None, amount__gt=0).values("id", "customer_id", "amount", "note", "date"):
    if imported(p["note"]): continue
    left = p["amount"] - alloc_by_pay[p["id"]]
    if left <= 0: continue
    if open_by_cust.get(p["customer_id"]): unapplied_with_debt.append((p, left))
    else: unapplied_no_debt += left; n_no_debt += 1
# 2. direct-invoice payments
direct = list(Payment.objects.exclude(invoice=None).values("id", "invoice_id", "amount"))
over = [i for i in Invoice.objects.filter(paid_amount__gt=0).values("id","number","total","paid_amount") if i["paid_amount"] > i["total"] and i["total"] > 0]
# 3. status vs money
paid_short = [i for i in inv if i["status"] == "paid" and i["paid_amount"] < i["total"]]
owed_full = [i for i in inv if i["status"] in OWED and i["paid_amount"] >= i["total"]]
cancelled_money = list(Invoice.objects.filter(status="cancelled", paid_amount__gt=0).values("number","paid_amount"))
# 4. paid_amount nobody can explain (imports, old credits, hand edits)
unexplained = []
for i in inv:
    known = alloc_by_inv[i["id"]] + direct_by_inv[i["id"]] + credit_by_inv[i["id"]]
    if abs(i["paid_amount"] - known) > Decimal("0.01"): unexplained.append((i, i["paid_amount"] - known))
# 5. account vs invoices
custs = {c.pk: c for c in Customer.objects.only("id", "customer_id", "full_name", "balance")}
settled_but_open = []; owing_nothing_open = []
for cid, c in custs.items():
    due = sum((i["total"] - i["paid_amount"] for i in open_by_cust.get(cid, [])), Z)
    if c.balance <= 0 and due > 0: settled_but_open.append((c, due))
    if c.balance > 0 and due == 0: owing_nothing_open.append(c)
    
def r(x): return f"R{x:,.2f}"
print("=== BILLING HEALTH (read-only) ===")
print(f"Customers {len(custs)} | open invoices {sum(len(v) for v in open_by_cust.values())} | payments {Payment.objects.count()} | allocations {PaymentAllocation.objects.count()}")
print(f"A. Payments with money NOT applied while the client has open invoices: {len(unapplied_with_debt)} ({r(sum((l for _, l in unapplied_with_debt), Z))})")
print(f"B. Payments with money not applied, client has nothing open (genuine credit): {n_no_debt} ({r(unapplied_no_debt)})")
print(f"C. Payments recorded straight against an invoice (no allocation row): {len(direct)} ({r(sum((d['amount'] for d in direct), Z))})")
print(f"D. Invoices paid MORE than their total: {len(over)}")
print(f"E. Invoices marked PAID but not fully paid: {len(paid_short)} ({r(sum((i['total']-i['paid_amount'] for i in paid_short), Z))} short)")
print(f"F. Invoices UNPAID/OVERDUE but fully paid: {len(owed_full)}")
print(f"G. CANCELLED invoices still holding money: {len(cancelled_money)} ({r(sum((i['paid_amount'] for i in cancelled_money), Z))})")
print(f"H. Invoices whose paid amount no payment/allocation/credit explains: {len(unexplained)} ({r(sum((d for _, d in unexplained), Z))} net)")
print(f"I. Clients paid up or in credit but with invoices still showing owed: {len(settled_but_open)} ({r(sum((d for _, d in settled_but_open), Z))} showing)")
print(f"J. Clients owing on balance but no open invoice explains it: {len(owing_nothing_open)} ({r(sum((c.balance for c in owing_nothing_open), Z))})")
print("--- worst I (paid up/credit but invoices show owed) ---")
for c, due in sorted(settled_but_open, key=lambda x: -x[1])[:15]:
    print(f"  {c.customer_id:<12} {c.full_name[:28]:<28} balance {r(c.balance):>12}  invoices show owed {r(due):>11}")
print("--- worst A (unapplied money while invoices open) ---")
for p, left in sorted(unapplied_with_debt, key=lambda x: -x[1])[:10]:
    c = custs.get(p["customer_id"]); print(f"  payment #{p['id']:<6} {c.customer_id if c else '?':<12} {p['date']:%d/%m/%Y}  unapplied {r(left)}")
print("--- E (marked paid, not paid) ---")
for i in paid_short[:10]: print(f"  {i['number']:<18} total {r(i['total'])} paid {r(i['paid_amount'])}")
print("--- J (owing, nothing open) ---")
for c in sorted(owing_nothing_open, key=lambda c: -c.balance)[:10]: print(f"  {c.customer_id:<12} {c.full_name[:28]:<28} balance {r(c.balance)}")
