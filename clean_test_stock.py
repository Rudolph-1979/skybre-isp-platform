# Skybre / Vexar -- clear out TEST stock (George, 14 Oct 2026).
#
# Removes every stock receipt (and its lines + attached invoice files),
# every stock issue / transfer (and lines), every stock movement and every
# serial unit, so each product reads 0 on hand. KEEPS products (names, SKUs,
# resell prices, barcodes), suppliers, supplier categories and stock sites.
#
# Run from ~/isp-platform:
#   dry run (counts only, changes nothing):
#     docker compose exec -T backend python manage.py shell < clean_test_stock.py
#   for real:
#     docker compose exec -T -e CONFIRM=yes backend python manage.py shell < clean_test_stock.py
import os

from django.db import transaction
from django.db.models import Sum

from inventory.models import (
    Product, SerializedUnit, StockIssue, StockIssueLine, StockMovement, StockReceipt, StockReceiptLine,
)

confirm = os.environ.get("CONFIRM", "").lower() == "yes"

counts = {
    "stock receipts": StockReceipt.objects.count(),
    "receipt lines": StockReceiptLine.objects.count(),
    "receipt attachments (files)": StockReceipt.objects.exclude(attachment="").exclude(attachment__isnull=True).count(),
    "stock issues / transfers": StockIssue.objects.count(),
    "issue lines": StockIssueLine.objects.count(),
    "stock movements": StockMovement.objects.count(),
    "serial units": SerializedUnit.objects.count(),
}
print("=" * 60)
print("TEST STOCK CLEAN-OUT --", "DELETING" if confirm else "DRY RUN (nothing changed)")
print("=" * 60)
for k, v in counts.items():
    print(f"  {k:32} {v}")
held = [(p.name, p.quantity_on_hand) for p in Product.objects.order_by("name") if p.quantity_on_hand]
print(f"  products with stock on hand now  {len(held)}")
for name, q in held:
    print(f"      {name}: {q}")
print(f"  products kept                    {Product.objects.count()}")

if not confirm:
    print("\nDry run only. Re-run with  -e CONFIRM=yes  to delete.")
else:
    files = [r.attachment.name for r in StockReceipt.objects.exclude(attachment="").exclude(attachment__isnull=True)]
    with transaction.atomic():
        StockMovement.objects.all().delete()
        StockIssueLine.objects.all().delete()
        StockIssue.objects.all().delete()
        SerializedUnit.objects.all().delete()
        StockReceiptLine.objects.all().delete()
        StockReceipt.objects.all().delete()
    # Files only after the database part has committed.
    from django.core.files.storage import default_storage
    removed = 0
    for name in files:
        try:
            default_storage.delete(name)
            removed += 1
        except Exception as exc:  # a missing file is not a reason to stop
            print(f"  (could not remove file {name}: {exc})")
    left = sum(1 for p in Product.objects.all() if p.quantity_on_hand)
    print(f"\nDone. Deleted the above; removed {removed} attachment file(s).")
    print(f"Products still showing stock: {left} (should be 0). Products kept: {Product.objects.count()}.")
