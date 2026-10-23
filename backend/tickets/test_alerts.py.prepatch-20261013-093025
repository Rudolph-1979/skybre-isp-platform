"""The dashboard tile's count and the Customers page's high_alert filter have
to return the same people. They share tickets.alerts; these tests are what
stops that sharing being quietly undone."""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from customers.models import Customer, Partner
from customers.views import CustomerViewSet
from accounts.views import HighAlertCustomersView
from tickets.models import Ticket

User = get_user_model()


class HighAlertTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="boss", password="x", role="admin")
        self.noisy = Customer.objects.create(full_name="Noisy Nick", email="n@x.com")
        self.quiet = Customer.objects.create(full_name="Quiet Quinn", email="q@x.com")

        today = datetime.date.today()
        for i in range(3):
            self._ticket(self.noisy, today)
        self._ticket(self.quiet, today)

    def _ticket(self, customer, on):
        t = Ticket.objects.create(customer=customer, subject="s", description="d")
        Ticket.objects.filter(pk=t.pk).update(
            created_at=datetime.datetime.combine(on, datetime.time(9, 0), tzinfo=datetime.timezone.utc)
        )
        return t

    def _tile(self):
        request = APIRequestFactory().get("/high-alert-customers/", {"months": 6, "min_tickets": 3})
        force_authenticate(request, user=self.admin)
        return HighAlertCustomersView.as_view()(request).data

    def _filtered_list(self):
        request = APIRequestFactory().get("/customers/", {"high_alert": "true"})
        force_authenticate(request, user=self.admin)
        return CustomerViewSet.as_view({"get": "list"})(request).data

    def test_filter_returns_the_flagged_customer_only(self):
        names = [r["full_name"] for r in self._filtered_list()["results"]]
        self.assertEqual(names, ["Noisy Nick"])

    def test_tile_count_matches_the_filtered_list(self):
        tile = self._tile()
        listed = self._filtered_list()
        self.assertEqual(tile["count"], listed["count"])
        self.assertEqual(
            sorted(r["customer"] for r in tile["results"]),
            sorted(r["id"] for r in listed["results"]),
        )

    def test_no_filter_returns_everyone(self):
        request = APIRequestFactory().get("/customers/")
        force_authenticate(request, user=self.admin)
        data = CustomerViewSet.as_view({"get": "list"})(request).data
        self.assertEqual(data["count"], 2)

    def test_tickets_spread_across_months_do_not_breach(self):
        """Three tickets is only a breach if they fall in ONE calendar month."""
        spread = Customer.objects.create(full_name="Spread Sam", email="s@x.com")
        today = datetime.date.today()
        for back in (0, 40, 80):
            self._ticket(spread, today - datetime.timedelta(days=back))
        names = [r["full_name"] for r in self._filtered_list()["results"]]
        self.assertNotIn("Spread Sam", names)

    def test_partner_scoping_is_respected(self):
        """A staff member restricted to one partner must not turn up a
        customer outside it through this filter."""
        mine = Partner.objects.create(name="Mine")
        theirs = Partner.objects.create(name="Theirs")
        self.noisy.partner = theirs
        self.noisy.save()
        scoped = User.objects.create_user(username="reseller", password="x", role="support")
        scoped.allowed_partners = [mine.id]
        scoped.save()

        request = APIRequestFactory().get("/customers/", {"high_alert": "true"})
        force_authenticate(request, user=scoped)
        data = CustomerViewSet.as_view({"get": "list"})(request).data
        self.assertEqual([r["full_name"] for r in data["results"]], [])
