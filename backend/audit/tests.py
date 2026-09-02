"""What the audit trail must get right to be worth having.

The failure modes worth testing here are not "does it write a row" but
the ones that make a trail actively misleading: recording nobody for a
real person's edit, recording a change that did not happen, leaking a
secret into a screen more people can read than the secret itself, and
losing the record when the thing it describes is deleted.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from audit.context import acting_as, acting_as_system
from audit.models import AuditEvent
from billing.models import Service, Tariff
from customers.models import Customer, Partner


class DiffTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="thandi", password="x", role=User.Role.SUPPORT,
            first_name="Thandi", last_name="M",
        )
        self.tariff = Tariff.objects.create(
            name="10 Mbps", price=Decimal("599.00"),
            speed_download_kbps=10240, speed_upload_kbps=10240,
        )
        self.customer = Customer.objects.create(
            full_name="Skybre Test", email="t@example.com", status=Customer.Status.ACTIVE
        )

    def test_a_create_is_recorded_against_the_person_who_made_it(self):
        with acting_as(self.staff):
            customer = Customer.objects.create(full_name="New Person", email="n@example.com")
        event = AuditEvent.objects.filter(target_type="customers.Customer", action="created").first()
        self.assertEqual(event.actor, self.staff)
        self.assertIn("thandi", event.actor_label)
        self.assertEqual(event.target_label, str(customer))

    def test_an_edit_records_the_field_that_moved_in_words(self):
        AuditEvent.objects.all().delete()
        with acting_as(self.staff):
            self.customer.status = Customer.Status.SUSPENDED
            self.customer.save()
        event = AuditEvent.objects.filter(action="updated", target_type="customers.Customer").first()
        change = next(c for c in event.changes if c["field"] == "status")
        # Human labels, not raw choice keys -- an audit row nobody can
        # read without the source code is not evidence of anything.
        self.assertEqual(change["from"], "Active")
        self.assertEqual(change["to"], "Suspended")

    def test_a_foreign_key_is_recorded_by_name_not_by_id(self):
        other = Tariff.objects.create(
            name="20 Mbps", price=Decimal("899.00"),
            speed_download_kbps=20480, speed_upload_kbps=20480,
        )
        service = Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-08-01",
        )
        AuditEvent.objects.all().delete()
        service.tariff = other
        service.save()
        event = AuditEvent.objects.filter(action="updated", target_type="billing.Service").first()
        change = next(c for c in event.changes if c["field"] == "tariff")
        self.assertEqual(change["from"], str(self.tariff))
        self.assertEqual(change["to"], str(other))

    def test_a_save_that_changes_nothing_records_nothing(self):
        """Otherwise every list refresh that re-saves a row reads as an
        edit, and the log fills with things nobody did."""
        AuditEvent.objects.all().delete()
        self.customer.save()
        self.assertFalse(AuditEvent.objects.filter(target_type="customers.Customer").exists())

    def test_a_password_change_is_recorded_but_never_its_value(self):
        AuditEvent.objects.all().delete()
        self.staff.set_password("a-new-password")
        self.staff.save()
        event = AuditEvent.objects.filter(action="updated", target_type="accounts.User").first()
        change = next(c for c in event.changes if c["field"] == "password")
        self.assertNotIn("a-new-password", str(event.changes))
        # Not the hash either. A hash is not a password, but it is the
        # thing you attack to get one.
        self.assertNotIn("pbkdf2", str(event.changes).lower())
        self.assertIn("changed", change["to"])

    def test_permission_changes_are_recorded_readably(self):
        AuditEvent.objects.all().delete()
        self.staff.allowed_sections = ["tickets", "customers"]
        self.staff.save()
        event = AuditEvent.objects.filter(action="updated", target_type="accounts.User").first()
        change = next(c for c in event.changes if c["field"] == "allowed_sections")
        self.assertEqual(change["from"], "(none)")
        self.assertEqual(change["to"], "tickets, customers")

    def test_a_scheduled_job_names_itself(self):
        AuditEvent.objects.all().delete()
        with acting_as_system("apply_cancellations (scheduled job)"):
            self.customer.status = Customer.Status.INACTIVE
            self.customer.save()
        event = AuditEvent.objects.filter(action="updated").first()
        self.assertIsNone(event.actor)
        self.assertIn("apply_cancellations", event.actor_label)


class SurvivalTests(TestCase):
    """The trail has to outlive what it describes, or it is missing
    exactly the events people go looking for."""

    def setUp(self):
        self.staff = User.objects.create_user(username="lerato", password="x", role=User.Role.ADMIN)
        self.customer = Customer.objects.create(full_name="Leaving Soon", email="l@example.com")

    def test_deleting_the_actor_keeps_the_record_of_what_they_did(self):
        with acting_as(self.staff):
            self.customer.status = Customer.Status.SUSPENDED
            self.customer.save()
        self.staff.delete()
        event = AuditEvent.objects.filter(action="updated", target_type="customers.Customer").first()
        self.assertIsNone(event.actor)
        self.assertIn("lerato", event.actor_label)

    def test_deleting_the_customer_keeps_the_record_of_the_deletion(self):
        name = str(self.customer)
        self.customer.delete()
        event = AuditEvent.objects.filter(action="deleted", target_type="customers.Customer").first()
        self.assertIsNotNone(event)
        self.assertEqual(event.target_label, name)


class ManyToManyTests(TestCase):
    def test_changing_a_sites_partners_is_recorded(self):
        """A through-table edit is invisible to a field diff, so it needs
        its own path -- and it is precisely the kind of change (who can
        see what) worth recording."""
        from network.models import NetworkSite

        partner = Partner.objects.create(name="Reseller A")
        site = NetworkSite.objects.create(title="Tower 1")
        AuditEvent.objects.all().delete()
        site.partners.add(partner)
        event = AuditEvent.objects.filter(target_type="network.NetworkSite", action="updated").first()
        self.assertIsNotNone(event)
        self.assertIn("Reseller A", str(event.changes))


class AuthEventTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="sipho", password="correct-horse-battery", role=User.Role.SUPPORT
        )

    def test_a_successful_sign_in_is_recorded(self):
        res = self.client.post(
            reverse("token_obtain_pair"),
            {"username": "sipho", "password": "correct-horse-battery"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        event = AuditEvent.objects.filter(action="login").first()
        self.assertEqual(event.actor, self.user)

    def test_a_failed_sign_in_is_recorded_against_the_name_tried(self):
        """Not against the user. Somebody guessing at another person's
        account must not fill that person's own history with failures
        they never made."""
        res = self.client.post(
            reverse("token_obtain_pair"),
            {"username": "sipho", "password": "wrong"},
            format="json",
        )
        self.assertEqual(res.status_code, 401)
        event = AuditEvent.objects.filter(action="login_failed").first()
        self.assertIsNotNone(event)
        self.assertIsNone(event.actor)
        self.assertEqual(event.actor_label, "sipho")
        self.assertNotIn("wrong", event.detail)

    def test_a_failed_sign_in_never_stores_the_password_tried(self):
        """A mistyped password is very often a real password with one
        character out of place, or the right password for a different
        system. The trail must not become a place to find them."""
        self.client.post(
            reverse("token_obtain_pair"),
            {"username": "sipho", "password": "MyOtherPassword123"},
            format="json",
        )
        self.assertFalse(AuditEvent.objects.filter(detail__icontains="MyOtherPassword123").exists())
        self.assertFalse(AuditEvent.objects.filter(actor_label__icontains="MyOtherPassword123").exists())


class AccessTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username="boss", password="x", role=User.Role.ADMIN)
        self.support = User.objects.create_user(
            username="desk", password="x", role=User.Role.SUPPORT, allowed_sections=["customers"]
        )
        self.customer = Customer.objects.create(full_name="Watched", email="w@example.com")

    def test_the_activity_log_needs_configs_access(self):
        self.client.force_authenticate(self.support)
        res = self.client.get("/api/audit-events/")
        self.assertEqual(res.status_code, 403)

    def test_an_admin_can_read_the_activity_log(self):
        self.client.force_authenticate(self.admin)
        res = self.client.get("/api/audit-events/")
        self.assertEqual(res.status_code, 200)

    def test_support_can_read_one_customers_history(self):
        """Support staff answering "why was my line cut off" need this;
        it is scoped to one customer, unlike the platform-wide log."""
        self.client.force_authenticate(self.support)
        res = self.client.get(f"/api/customers/{self.customer.pk}/history/")
        self.assertEqual(res.status_code, 200)

    def test_the_log_cannot_be_edited_or_deleted_through_the_api(self):
        """A trail the people it records can rewrite is not a trail."""
        with acting_as(self.admin):
            self.customer.status = Customer.Status.SUSPENDED
            self.customer.save()
        event = AuditEvent.objects.filter(action="updated").first()
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.delete(f"/api/audit-events/{event.pk}/").status_code, 405)
        self.assertEqual(
            self.client.patch(f"/api/audit-events/{event.pk}/", {"detail": "no"}, format="json").status_code,
            405,
        )

    def test_partner_scoping_applies_to_a_customers_history(self):
        """Otherwise the History tab becomes the hole in a restriction
        that is enforced everywhere else."""
        mine = Partner.objects.create(name="Mine")
        theirs = Partner.objects.create(name="Theirs")
        hidden = Customer.objects.create(full_name="Not Mine", email="h@example.com", partner=theirs)
        self.support.allowed_partners = [mine.pk]
        self.support.save()
        self.client.force_authenticate(self.support)
        res = self.client.get(f"/api/customers/{hidden.pk}/history/")
        # 404, not 403 -- a 403 confirms the id exists.
        self.assertEqual(res.status_code, 404)


class CustomerSessionsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(username="ops", password="x", role=User.Role.ADMIN)
        self.tariff = Tariff.objects.create(
            name="T", price=Decimal("1"), speed_download_kbps=1024, speed_upload_kbps=1024
        )
        self.customer = Customer.objects.create(full_name="Online Person", email="o@example.com")
        self.service = Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-08-01", radius_username="online1",
        )

    def test_a_live_session_reports_a_real_duration(self):
        """acctsessiontime is only written on an interim update or a
        stop, so a session four minutes old has NULL there. Reporting
        that as 0m makes a working connection look like a failed one."""
        import datetime

        from django.utils import timezone

        from radiusauth.models import RadAcct

        RadAcct.objects.create(
            acctsessionid="s1", acctuniqueid="u1", username="online1", nasipaddress="10.0.0.1",
            acctstarttime=timezone.now() - datetime.timedelta(minutes=40),
            acctsessiontime=None, framedipaddress="102.23.1.5",
            acctinputoctets=1000, acctoutputoctets=9000,
        )
        self.client.force_authenticate(self.staff)
        res = self.client.get(f"/api/customers/{self.customer.pk}/sessions/")
        self.assertEqual(res.status_code, 200)
        row = res.data["results"][0]
        self.assertTrue(row["active"])
        self.assertGreater(row["duration_seconds"], 2000)
        # Customer's point of view, not the NAS's: what the NAS received
        # is the customer's upload.
        self.assertEqual(row["download_bytes"], 9000)
        self.assertEqual(row["upload_bytes"], 1000)

    def test_a_customer_with_no_radius_username_gets_an_empty_list(self):
        self.service.radius_username = ""
        self.service.save()
        self.client.force_authenticate(self.staff)
        res = self.client.get(f"/api/customers/{self.customer.pk}/sessions/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["results"], [])


class CustomerDeleteCascadeTests(TestCase):
    """Deleting a customer must survive the audit rows written for
    everything that cascades away with them.

    Regression: each tracked child (service, invoice, payment, ticket,
    task) writes a "deleted" audit row mid-cascade, and those rows were
    linked to the customer being deleted. The collector had already
    decided which AuditEvent rows to null out before those rows existed,
    so the link survived, pointed at a deleted row, and the DEFERRABLE FK
    failed the whole transaction at COMMIT. Symptom: deleting a customer
    who had ever had a ticket raised IntegrityError and deleted nothing.
    """

    def test_deleting_a_customer_with_tracked_children_succeeds(self):
        from customers.models import Customer, CustomerTask
        from tickets.models import Ticket

        customer = Customer.objects.create(full_name="Cascade Co")
        ticket = Ticket.objects.create(customer=customer, subject="Line down")
        ticket.status = Ticket.Status.RESOLVED
        ticket.save()
        task = CustomerTask.objects.create(customer=customer, title="Call them back")
        task.status = CustomerTask.Status.DONE
        task.save()

        customer_id = customer.pk
        customer.delete()

        self.assertFalse(Customer.objects.filter(pk=customer_id).exists())
        self.assertFalse(Ticket.objects.filter(pk=ticket.pk).exists())
        self.assertFalse(CustomerTask.objects.filter(pk=task.pk).exists())

    def test_the_deletion_events_are_still_recorded(self):
        """The customer link is dropped, not the audit row -- "who deleted
        this customer, and when" is the one question this table exists to
        answer about a customer that no longer exists."""
        from customers.models import Customer, CustomerTask

        customer = Customer.objects.create(full_name="Still Recorded Co")
        CustomerTask.objects.create(customer=customer, title="Goes away too")
        customer.delete()

        self.assertTrue(
            AuditEvent.objects.filter(
                action="deleted", target_type="customers.Customer"
            ).exists()
        )
        task_event = AuditEvent.objects.filter(
            action="deleted", target_type="customers.CustomerTask"
        ).first()
        self.assertIsNotNone(task_event)
        self.assertIsNone(task_event.customer)

    def test_an_unrelated_customer_keeps_its_link(self):
        """The flag must not leak: a second customer's task events, written
        after the first customer's delete, still link correctly."""
        from customers.models import Customer, CustomerTask

        doomed = Customer.objects.create(full_name="Doomed Co")
        CustomerTask.objects.create(customer=doomed, title="Bye")
        doomed.delete()

        survivor = Customer.objects.create(full_name="Survivor Co")
        task = CustomerTask.objects.create(customer=survivor, title="Keep me")
        task.priority = CustomerTask.Priority.HIGH
        task.save()

        self.assertTrue(
            AuditEvent.objects.filter(
                customer=survivor, target_type="customers.CustomerTask", action="updated"
            ).exists()
        )
