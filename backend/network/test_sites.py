"""A site serving several partners, and the migration that got it there."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from customers.models import Partner
from network.models import NetworkSite
from network.views import NetworkSiteViewSet

User = get_user_model()


class SitePartnersTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="boss", password="x", role="admin")
        self.a = Partner.objects.create(name="Alpha")
        self.b = Partner.objects.create(name="Beta")
        self.factory = APIRequestFactory()

    def _post(self, payload):
        request = self.factory.post("/x", payload, format="json")
        force_authenticate(request, user=self.staff)
        return NetworkSiteViewSet.as_view({"post": "create"})(request)

    def _list(self):
        request = self.factory.get("/x")
        force_authenticate(request, user=self.staff)
        return NetworkSiteViewSet.as_view({"get": "list"})(request)

    def test_a_site_can_serve_several_partners(self):
        response = self._post({"title": "Kroonstad Tower", "partners": [self.a.pk, self.b.pk]})
        self.assertEqual(response.status_code, 201)
        site = NetworkSite.objects.get()
        self.assertEqual(set(site.partners.values_list("pk", flat=True)), {self.a.pk, self.b.pk})

    def test_names_come_back_for_the_list(self):
        self._post({"title": "T", "partners": [self.a.pk, self.b.pk]})
        row = self._list().data["results"][0]
        self.assertEqual(sorted(row["partner_names"]), ["Alpha", "Beta"])

    def test_no_partners_is_allowed_and_means_all(self):
        response = self._post({"title": "Open Tower"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self._list().data["results"][0]["partner_names"], [])

    def test_partners_can_be_changed(self):
        self._post({"title": "T", "partners": [self.a.pk]})
        site = NetworkSite.objects.get()
        request = self.factory.patch("/x", {"partners": [self.b.pk]}, format="json")
        force_authenticate(request, user=self.staff)
        response = NetworkSiteViewSet.as_view({"patch": "partial_update"})(request, pk=site.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(site.partners.values_list("name", flat=True)), ["Beta"])

    def test_listing_sites_does_not_query_per_row(self):
        """partner_names crosses a many-to-many; without the prefetch it is one
        query per site."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        for i in range(6):
            self._post({"title": f"Tower {i}", "partners": [self.a.pk, self.b.pk]})
        with CaptureQueriesContext(connection) as ctx:
            self._list()
        self.assertLessEqual(len(ctx), 6, f"{len(ctx)} queries for 6 sites")
