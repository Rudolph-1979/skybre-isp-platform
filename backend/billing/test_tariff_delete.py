"""Deleting a tariff, and refusing to when something depends on it."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from billing.models import Service, Tariff
from billing.views import TariffViewSet
from customers.models import Customer

User = get_user_model()


class TariffDeleteTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="boss", password="x", role="admin")
        self.spare = Tariff.objects.create(name="Spare", price=Decimal("1"),
                                           speed_download_kbps=1024, speed_upload_kbps=1024)
        self.customer = Customer.objects.create(full_name="Del Dave", email="d@x.com")

    def _delete(self, tariff):
        request = APIRequestFactory().delete(f"/tariffs/{tariff.pk}/")
        force_authenticate(request, user=self.admin)
        return TariffViewSet.as_view({"delete": "destroy"})(request, pk=tariff.pk)

    def test_unused_tariff_deletes(self):
        doomed = Tariff.objects.create(name="ZZ-RADIUS-TEST (delete me)", price=Decimal("1"),
                                       speed_download_kbps=20, speed_upload_kbps=5)
        response = self._delete(doomed)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Tariff.objects.filter(pk=doomed.pk).exists())

    def test_tariff_in_use_is_refused_with_a_useful_message(self):
        used = Tariff.objects.create(name="Live plan", price=Decimal("1"),
                                     speed_download_kbps=1024, speed_upload_kbps=1024)
        Service.objects.create(customer=self.customer, tariff=used, status=Service.Status.ACTIVE,
                               start_date="2026-08-01")
        response = self._delete(used)
        self.assertEqual(response.status_code, 400)
        detail = response.data["detail"]
        self.assertIn("1 service is on it", detail)
        self.assertIn("untick Active", detail)
        self.assertTrue(Tariff.objects.filter(pk=used.pk).exists())

    def test_a_booked_change_also_blocks_deletion(self):
        """pending_tariff is SET_NULL, so without the guard this would delete
        cleanly and leave a service with a change date and no tariff -- the
        exact half-a-booking state the serializer refuses to let staff make."""
        booked = Tariff.objects.create(name="Future plan", price=Decimal("2"),
                                       speed_download_kbps=2048, speed_upload_kbps=2048)
        Service.objects.create(customer=self.customer, tariff=self.spare,
                               status=Service.Status.ACTIVE, start_date="2026-08-01",
                               pending_tariff=booked, pending_tariff_date="2026-12-01")
        response = self._delete(booked)
        self.assertEqual(response.status_code, 400)
        self.assertIn("booked onto it", response.data["detail"])
        self.assertTrue(Tariff.objects.filter(pk=booked.pk).exists())

    def test_plurals_read_correctly(self):
        used = Tariff.objects.create(name="Busy", price=Decimal("1"),
                                     speed_download_kbps=1024, speed_upload_kbps=1024)
        for _ in range(3):
            Service.objects.create(customer=self.customer, tariff=used,
                                   status=Service.Status.ACTIVE, start_date="2026-08-01")
        self.assertIn("3 services are on it", self._delete(used).data["detail"])
