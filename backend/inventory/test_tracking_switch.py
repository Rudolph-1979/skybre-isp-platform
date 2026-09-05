"""Changing how an existing product is tracked.

The bug these exist for: quantity_on_hand is read from StockMovement rows
for a quantity product and from SerializedUnit rows for a serialized one.
The Tracking dropdown on the ordinary product edit form was writable with
nothing checking it, so switching it on a product that already had stock
answered 200 and the on-hand figure went to 0 -- 305m of cable on the shelf
reading as none in stock, and the issue screen then refusing to issue it.
The receipt-import path had refused this switch for the same reason all
along; the product edit form had not.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Product, SerializedUnit, StockMovement


class TrackingTypeSwitchTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(username="stores", password="x", role=User.Role.ADMIN)
        self.client.force_authenticate(self.staff)
    def test_switching_a_stocked_quantity_product_to_serialized_is_refused(self):
        """305m of cable is on the shelf. Changing the Tracking dropdown used
        to answer 200 and leave the catalogue reading 0 in stock, because the
        count is then taken from serialized units, of which there are none."""
        product = Product.objects.create(
            name="Cat6 outdoor cable", tracking_type=Product.TrackingType.QUANTITY
        )
        StockMovement.objects.create(
            product=product,
            movement_type=StockMovement.MovementType.RECEIPT,
            quantity=305,
        )

        res = self.client.patch(
            f"/api/products/{product.pk}/", {"tracking_type": "serialized"}, format="json"
        )

        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn("already has quantity-tracked stock", str(res.data["tracking_type"][0]))
        product.refresh_from_db()
        self.assertEqual(product.tracking_type, Product.TrackingType.QUANTITY)
        self.assertEqual(product.quantity_on_hand, 305, "the cable stopped being counted")
    def test_switching_a_product_with_units_on_the_shelf_to_quantity_is_refused(self):
        """The same loss the other way round: a router whose units are booked
        in by serial has no StockMovement history at all, so as a quantity
        product it counts as 0 and every unit vanishes from the figure."""
        product = Product.objects.create(
            name="Ubiquiti LiteBeam", tracking_type=Product.TrackingType.SERIALIZED
        )
        SerializedUnit.objects.create(product=product, serial_number="LBE-0001")

        res = self.client.patch(
            f"/api/products/{product.pk}/", {"tracking_type": "quantity"}, format="json"
        )

        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn("already has individually-tracked units", str(res.data["tracking_type"][0]))
        product.refresh_from_db()
        self.assertEqual(product.tracking_type, Product.TrackingType.SERIALIZED)
        self.assertEqual(product.quantity_on_hand, 1)
    def test_opening_stock_sent_on_an_edit_cannot_flip_a_stocked_product(self):
        """Opening stock is create-only -- update() drops it. Sending it on an
        edit used to switch the product to serialized anyway, without the
        Tracking field being touched, so 500 connectors became 0 in stock and
        no unit was created to replace them."""
        product = Product.objects.create(
            name="RJ45 connector", tracking_type=Product.TrackingType.QUANTITY
        )
        StockMovement.objects.create(
            product=product,
            movement_type=StockMovement.MovementType.RECEIPT,
            quantity=500,
        )

        res = self.client.patch(
            f"/api/products/{product.pk}/",
            {"initial_units": [{"serial": "RJ-0001", "mac": ""}]},
            format="json",
        )

        self.assertEqual(res.status_code, 200, res.data)
        product.refresh_from_db()
        self.assertEqual(product.tracking_type, Product.TrackingType.QUANTITY)
        self.assertEqual(product.quantity_on_hand, 500)
        self.assertFalse(product.serialized_units.exists(), "an edit must not create units")
