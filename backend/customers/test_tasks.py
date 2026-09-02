"""CustomerTask: the completed_at/status invariant, overdue, and who can
reach the endpoint.

The access tests are the ones that matter most here. Tasks are internal
notes about chasing a customer, so two things must hold that don't hold
for tickets: a portal login must get nothing at all, and a staff member
restricted to certain reseller partners must not be able to read tasks
for customers the Customers page already hides from them.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from customers.models import Customer, CustomerTask, Partner

User = get_user_model()


class CustomerTaskModelTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(full_name="Task Model Co")

    def test_moving_to_done_stamps_completed_at(self):
        task = CustomerTask.objects.create(customer=self.customer, title="Phone them back")
        self.assertIsNone(task.completed_at)
        task.status = CustomerTask.Status.DONE
        task.save()
        task.refresh_from_db()
        self.assertIsNotNone(task.completed_at)

    def test_reopening_clears_completed_at(self):
        """A task closed by mistake and reopened must not keep a
        completion time that has already been read as fact."""
        task = CustomerTask.objects.create(
            customer=self.customer, title="Closed too early", status=CustomerTask.Status.DONE
        )
        self.assertIsNotNone(task.completed_at)
        task.status = CustomerTask.Status.OPEN
        task.save()
        task.refresh_from_db()
        self.assertIsNone(task.completed_at)

    def test_cancelling_does_not_count_as_completed(self):
        task = CustomerTask.objects.create(
            customer=self.customer, title="Not needed after all", status=CustomerTask.Status.CANCELLED
        )
        self.assertIsNone(task.completed_at)
        self.assertFalse(task.is_outstanding)

    def test_overdue_only_applies_to_outstanding_tasks_with_a_due_date(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        overdue = CustomerTask.objects.create(
            customer=self.customer, title="Late", due_date=yesterday
        )
        no_due_date = CustomerTask.objects.create(customer=self.customer, title="Someday")
        finished_late = CustomerTask.objects.create(
            customer=self.customer, title="Late but done",
            due_date=yesterday, status=CustomerTask.Status.DONE,
        )
        self.assertTrue(overdue.is_overdue)
        self.assertFalse(no_due_date.is_overdue)
        self.assertFalse(finished_late.is_overdue)


class CustomerTaskAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="support1", password="pw-for-tests", role=User.Role.SUPPORT
        )
        self.customer = Customer.objects.create(full_name="API Task Co", email="api@example.com")

    def test_staff_can_create_a_task_and_created_by_is_stamped(self):
        self.client.force_authenticate(self.staff)
        res = self.client.post(
            "/api/customer-tasks/",
            {"customer": self.customer.id, "title": "Chase the debit order"},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        task = CustomerTask.objects.get(pk=res.data["id"])
        self.assertEqual(task.created_by, self.staff)
        self.assertEqual(task.status, CustomerTask.Status.OPEN)

    def test_created_by_cannot_be_spoofed(self):
        other = User.objects.create_user(
            username="someone-else", password="pw-for-tests", role=User.Role.SALES
        )
        self.client.force_authenticate(self.staff)
        res = self.client.post(
            "/api/customer-tasks/",
            {"customer": self.customer.id, "title": "Filed as me", "created_by": other.id},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(CustomerTask.objects.get(pk=res.data["id"]).created_by, self.staff)

    def test_blank_title_is_rejected(self):
        self.client.force_authenticate(self.staff)
        res = self.client.post(
            "/api/customer-tasks/", {"customer": self.customer.id, "title": "   "}, format="json"
        )
        self.assertEqual(res.status_code, 400)

    def test_outstanding_filter_matches_the_models_definition(self):
        CustomerTask.objects.create(customer=self.customer, title="Open one")
        CustomerTask.objects.create(
            customer=self.customer, title="Busy", status=CustomerTask.Status.IN_PROGRESS
        )
        CustomerTask.objects.create(
            customer=self.customer, title="Finished", status=CustomerTask.Status.DONE
        )
        self.client.force_authenticate(self.staff)
        res = self.client.get("/api/customer-tasks/?outstanding=true")
        self.assertEqual(res.status_code, 200)
        titles = {row["title"] for row in res.data["results"]}
        self.assertEqual(titles, {"Open one", "Busy"})

    def test_a_portal_customer_gets_nothing_from_this_endpoint(self):
        """Tasks are staff notes about the customer, not the customer's
        own record -- unlike tickets, which they are meant to see."""
        CustomerTask.objects.create(customer=self.customer, title="Internal note about them")
        portal_user = User.objects.create_user(
            username="portal-user", password="pw-for-tests", role=User.Role.CUSTOMER
        )
        self.customer.user = portal_user
        self.customer.save()
        self.client.force_authenticate(portal_user)
        res = self.client.get("/api/customer-tasks/")
        self.assertEqual(res.status_code, 403)

    def test_partner_restricted_staff_cannot_see_another_partners_tasks(self):
        mine = Partner.objects.create(name="My Reseller")
        theirs = Partner.objects.create(name="Someone Else's Reseller")
        my_customer = Customer.objects.create(full_name="Mine", partner=mine)
        their_customer = Customer.objects.create(full_name="Theirs", partner=theirs)
        direct_customer = Customer.objects.create(full_name="Direct")
        CustomerTask.objects.create(customer=my_customer, title="Mine to do")
        CustomerTask.objects.create(customer=their_customer, title="Not mine to see")
        CustomerTask.objects.create(customer=direct_customer, title="Direct customer task")

        restricted = User.objects.create_user(
            username="reseller-support", password="pw-for-tests",
            role=User.Role.SUPPORT, allowed_partners=[mine.id],
        )
        self.client.force_authenticate(restricted)
        res = self.client.get("/api/customer-tasks/")
        self.assertEqual(res.status_code, 200)
        titles = {row["title"] for row in res.data["results"]}
        # Direct (no-partner) customers stay visible -- they aren't owned
        # by any reseller, same convention as CustomerViewSet.
        self.assertEqual(titles, {"Mine to do", "Direct customer task"})

    def test_staff_without_the_customers_section_are_refused(self):
        CustomerTask.objects.create(customer=self.customer, title="Hidden by section")
        narrowed = User.objects.create_user(
            username="stock-only", password="pw-for-tests",
            role=User.Role.SUPPORT, allowed_sections=["inventory"],
        )
        self.client.force_authenticate(narrowed)
        res = self.client.get("/api/customer-tasks/")
        self.assertEqual(res.status_code, 403)

    def test_deleting_a_customer_takes_its_tasks_with_it(self):
        task = CustomerTask.objects.create(customer=self.customer, title="Goes away with them")
        self.customer.delete()
        self.assertFalse(CustomerTask.objects.filter(pk=task.pk).exists())


class CustomerTaskHistoryTests(TestCase):
    """The audit registry entry -- a task's changes should land on the
    customer's History tab, which is the whole reason it's registered."""

    def test_task_changes_are_recorded_against_the_customer(self):
        from audit.models import AuditEvent

        customer = Customer.objects.create(full_name="History Co")
        task = CustomerTask.objects.create(customer=customer, title="Watch this change")
        task.status = CustomerTask.Status.DONE
        task.save()
        self.assertTrue(
            AuditEvent.objects.filter(
                customer=customer, target_type="customers.CustomerTask", action="updated"
            ).exists()
        )
