"""Equipment & router details tile (20 Oct 2026, Rudolph: "Add a tile to add
router details of ptp or something" / "Clean Typeable information tile").
Free text, staff only -- never returned to the client's own login."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from customers.models import Customer

User = get_user_model()


class EquipmentDetailsTests(TestCase):
    def setUp(self):
        self.c = Customer.objects.create(full_name="Andre Visagie", status=Customer.Status.ACTIVE, notes="staff remark")

    def test_staff_type_and_read(self):
        api = APIClient()
        api.force_authenticate(User.objects.create_user(username="liesi", password="x", role="support",
                                                         allowed_sections=["customers"]))
        text = "PtP: LiteBeam 5AC - 10.20.1.15 - admin / secret\nRouter: hAP ac2"
        res = api.patch(f"/api/customers/{self.c.pk}/", {"equipment_details": text}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(api.get(f"/api/customers/{self.c.pk}/").data["equipment_details"], text)

    def test_client_never_sees_it_or_staff_notes(self):
        self.c.equipment_details = "Router admin / secret"
        self.c.save()
        user = User.objects.create_user(username="andre", password="x", role="customer")
        self.c.user = user
        self.c.save()
        api = APIClient()
        api.force_authenticate(user)
        res = api.get(f"/api/customers/{self.c.pk}/")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertNotIn("equipment_details", res.data)
        self.assertNotIn("notes", res.data)
