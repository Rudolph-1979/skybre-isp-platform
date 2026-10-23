"""The Products list, when someone fat-fingers a resell price.

The bug these exist for: ProductSerializer declared margin_pct as
DecimalField(max_digits=6), and margin_pct divides by the sell price --
which is typed by hand. A R25,000 fibre splicer with its resell price keyed
as R10 gives -249900.0%, seven digits. DRF quantizes output under a decimal
context whose precision IS max_digits, so seven digits raised
decimal.InvalidOperation. That is not an APIException, so it escaped the
serializer as a 500 and took the ENTIRE catalogue with it -- the Inventory
tab went blank, and so did every product picker that reads this endpoint
(Finance invoice lines, quotes, Services), for every product, not just the
mistyped one.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Product, StockReceipt, StockReceiptLine, Supplier


class ProductMarginTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="stores", password="x", role=User.Role.ADMIN
        )
        self.client.force_authenticate(self.staff)
        self.supplier = Supplier.objects.create(name="Miro")
        # One sane row and one mistyped row, listed together -- which is the
        # whole point: the sane row must survive its neighbour.
        self.sane = self._product_costing("Ubiquiti LiteBeam", "1200.00", "1500.00")
        self.mistyped = self._product_costing("Fibre splicer", "25000.00", "10.00")

    def _product_costing(self, name, unit_cost, sell_price):
        """A catalogue entry with one delivery behind it, so
        latest_cost_excl_vat -- and therefore margin_pct -- is not None."""
        product = Product.objects.create(name=name, sell_price=Decimal(sell_price))
        receipt = StockReceipt.objects.create(
            supplier=self.supplier,
            invoice_number=f"INV-{product.pk}",
            invoice_date=date(2026, 8, 27),
        )
        StockReceiptLine.objects.create(
            receipt=receipt,
            product=product,
            quantity=1,
            unit_cost=Decimal(unit_cost),
            vat_rate_pct=Decimal("15"),
        )
        return product

    def test_a_mistyped_resell_price_does_not_blank_the_whole_products_list(self):
        """R10 keyed on a R25,000 splicer used to 500 /api/products/, which
        emptied the Inventory tab and every product picker in Finance and
        Services -- the sane products disappeared along with the bad one."""
        res = self.client.get("/api/products/")

        self.assertEqual(res.status_code, 200, getattr(res, "data", res.content))
        names = {row["name"] for row in res.data["results"]}
        self.assertIn(self.sane.name, names, "one bad row took the whole catalogue with it")
        self.assertIn(self.mistyped.name, names)
    def test_an_impossible_margin_is_reported_rather_than_swallowed(self):
        """The absurd number is the point -- it is how stores staff spot the
        typo, and the Inventory column already paints a negative margin red.
        Clamping or nulling it would leave a wrong resell price sitting
        quietly on the catalogue until it reached a customer's invoice."""
        res = self.client.get("/api/products/")

        row = next(r for r in res.data["results"] if r["name"] == self.mistyped.name)
        self.assertEqual(Decimal(row["margin_pct"]), Decimal("-249900.0"))
    def test_an_ordinary_margin_still_reads_to_one_decimal_place(self):
        """Bought at R1,200, sold at R1,500 is a 20.0% margin -- margin, not
        the 25% markup. Widening the field must not disturb that: the
        Inventory column renders the value with toFixed(1)."""
        res = self.client.get("/api/products/")

        row = next(r for r in res.data["results"] if r["name"] == self.sane.name)
        self.assertEqual(Decimal(row["margin_pct"]), Decimal("20.0"))
