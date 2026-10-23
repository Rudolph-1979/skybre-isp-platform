"""A new service taking over its predecessor's PPPoE login and IP
(package #22).

Rudolph, 24 Sep: creating Christo's new Carfone service with his EXISTING
PPPoE username failed ("service with this radius username already
exists" -- a service's username is reserved for good, even once
terminated), and the IP would not carry over either (nothing frees a
terminated service's Customer Pool address). He confirmed the new-service
design is what he wants regardless: "Having a new service between service
changes seems like the best option." This is what makes that work: the new
service names the old one it REPLACES and takes over its login/IP -- at
cutover for a service that starts itself later, or immediately for one
created already Active.
"""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from customers.models import Customer, Partner
from network.models import IPAddress, IPPool

from .models import Service, Tariff
from .scheduled_changes import apply_due_scheduled_changes, apply_service_handover
from .serializers import ServiceSerializer

MOVE = datetime.date(2026, 10, 1)


class _Setup(TestCase):
    def setUp(self):
        self.vuma = Partner.objects.create(name="Vumatel Fibre")
        self.carfone = Partner.objects.create(name="Carfone Fibre")
        self.vuma_30 = Tariff.objects.create(name="Vuma BF 30/30 Mbps", price=Decimal("449.00"),
                                             tax_rate_pct=Decimal("15.00"), partner=self.vuma)
        self.carfone_50 = Tariff.objects.create(name="Carfone 50 Mbps", price=Decimal("599.00"),
                                                tax_rate_pct=Decimal("15.00"), partner=self.carfone)
        self.christo = Customer.objects.create(
            full_name="Christo Van Den Heever", customer_id="CUS-001603", email="christo@example.com",
            status=Customer.Status.ACTIVE, partner=self.carfone,
        )
        self.pool = IPPool.objects.create(name="Customer Pool", category=IPPool.Category.CUSTOMER,
                                          network_cidr="102.23.154.0/29")
        self.address = IPAddress.objects.create(pool=self.pool, address="102.23.154.6")
        self.vuma_line = Service.objects.create(
            customer=self.christo, tariff=self.vuma_30, status=Service.Status.ACTIVE,
            start_date=datetime.date(2026, 9, 24), end_date=MOVE,
            radius_username="christo_vuma", radius_password="secret1",
            radius_connection_type=Service.ConnectionType.PPPOE,
            ip_assignment_mode=Service.IPAssignmentMode.POOL, ip_pool=self.pool,
        )
        self.address.status = IPAddress.Status.ASSIGNED
        self.address.assigned_service = self.vuma_line
        self.address.save(update_fields=["status", "assigned_service"])


class ApplyServiceHandoverTests(_Setup):
    """The function scheduled_changes calls at cutover, and create() calls
    immediately -- exercised directly so the DB state is easy to assert."""

    def _new_service(self, **overrides):
        kwargs = dict(
            customer=self.christo, tariff=self.carfone_50, status=Service.Status.PENDING,
            start_date=MOVE, activate_on=MOVE, replaces=self.vuma_line,
            radius_connection_type=Service.ConnectionType.PPPOE,
            ip_assignment_mode=Service.IPAssignmentMode.POOL, ip_pool=self.pool,
        )
        kwargs.update(overrides)
        return Service.objects.create(**kwargs)

    def test_takes_over_the_username_password_and_ip(self):
        new = self._new_service()
        apply_service_handover(new)
        new.save(update_fields=["radius_username", "radius_password"])
        new.refresh_from_db()
        self.vuma_line.refresh_from_db()
        self.address.refresh_from_db()
        self.assertEqual(new.radius_username, "christo_vuma")
        self.assertEqual(new.radius_password, "secret1")
        self.assertIsNone(self.vuma_line.radius_username)
        self.assertIsNone(self.vuma_line.radius_password)
        self.assertEqual(self.address.assigned_service_id, new.pk)
        self.assertEqual(self.address.status, IPAddress.Status.ASSIGNED)

    def test_an_explicit_username_is_left_alone(self):
        new = self._new_service(radius_username="christo_carfone", radius_password="secret2")
        apply_service_handover(new)
        self.assertEqual(new.radius_username, "christo_carfone")
        self.vuma_line.refresh_from_db()
        self.assertEqual(self.vuma_line.radius_username, "christo_vuma", "an untaken username stays put")

    def test_a_service_with_no_replaces_is_untouched(self):
        new = self._new_service(replaces=None, radius_username=None)
        apply_service_handover(new)
        self.assertIsNone(new.radius_username)

    def test_at_cutover_via_the_scheduled_job(self):
        self._new_service()
        apply_due_scheduled_changes(as_of=MOVE, commit=True)
        new = Service.objects.get(customer=self.christo, tariff=self.carfone_50)
        self.vuma_line.refresh_from_db()
        self.address.refresh_from_db()
        self.assertEqual(new.status, Service.Status.ACTIVE)
        self.assertEqual(new.radius_username, "christo_vuma")
        self.assertIsNone(self.vuma_line.radius_username)
        self.assertEqual(self.address.assigned_service_id, new.pk)

    def test_immediately_for_a_service_created_already_active(self):
        """Starts: Now -- never goes through scheduled_changes."""
        factory = APIRequestFactory()
        request = factory.post("/api/services/")
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(username="staff1", password="x")
        force_authenticate(request, user=user)
        request.user = user
        serializer = ServiceSerializer(
            data={
                "customer": self.christo.pk, "tariff": self.carfone_50.pk, "status": "active",
                "start_date": "2026-10-01", "replaces": self.vuma_line.pk,
                "radius_connection_type": "pppoe", "ip_assignment_mode": "pool", "ip_pool": self.pool.pk,
            },
            context={"request": request},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        new = serializer.save()
        self.vuma_line.refresh_from_db()
        self.address.refresh_from_db()
        self.assertEqual(new.radius_username, "christo_vuma")
        self.assertIsNone(self.vuma_line.radius_username)
        self.assertEqual(self.address.assigned_service_id, new.pk)


class ServiceSerializerReplacesValidationTests(_Setup):
    def _serialize(self, **data):
        base = dict(
            customer=self.christo.pk, tariff=self.carfone_50.pk, status="pending",
            start_date=MOVE.isoformat(), activate_on=MOVE.isoformat(),
        )
        base.update(data)
        return ServiceSerializer(data=base)

    def test_a_booked_move_is_accepted(self):
        s = self._serialize(replaces=self.vuma_line.pk)
        self.assertTrue(s.is_valid(), s.errors)

    def test_refuses_another_customers_service(self):
        other = Customer.objects.create(full_name="Other Oscar", customer_id="CUS-009999",
                                        email="oscar@example.com", status=Customer.Status.ACTIVE)
        other_line = Service.objects.create(customer=other, tariff=self.vuma_30, status=Service.Status.ACTIVE)
        s = self._serialize(replaces=other_line.pk)
        self.assertFalse(s.is_valid())
        self.assertIn("replaces", s.errors)

    def test_refuses_a_service_that_already_has_a_successor(self):
        Service.objects.create(
            customer=self.christo, tariff=self.carfone_50, status=Service.Status.PENDING,
            start_date=MOVE, activate_on=MOVE, replaces=self.vuma_line,
        )
        s = self._serialize(replaces=self.vuma_line.pk)
        self.assertFalse(s.is_valid())
        self.assertIn("replaces", s.errors)

    def test_refuses_starting_before_the_old_ones_end_date(self):
        s = self._serialize(replaces=self.vuma_line.pk, activate_on="2026-09-30", start_date="2026-09-30")
        self.assertFalse(s.is_valid())
        self.assertIn("replaces", s.errors)

    def test_no_replaces_is_fine(self):
        s = self._serialize()
        self.assertTrue(s.is_valid(), s.errors)
