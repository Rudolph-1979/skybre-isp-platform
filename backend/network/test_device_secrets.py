"""The device API must not read back the credentials the admin hides.

network/admin.py carries `exclude = ("api_password", "snmp_community")`
on the grounds that these are "the credentials the REST API is careful to
keep write_only with a *_set boolean". The API was careful about exactly
one of them. api_password was write_only with api_password_set beside it;
snmp_community sat in Meta.fields as an ordinary readable string, so
GET /api/devices/ handed the live community back to every staff member
who could open the Networking page -- while the admin form two files over
refused to show it to the same person.

The SNMP community is the read password for every box on the network.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from network.models import Device

User = get_user_model()

COMMUNITY = "s3cret-community"


class DeviceSecretsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.device = Device.objects.create(
            name="Teraco Core", device_type="router", ip_address="10.0.0.1",
            snmp_community=COMMUNITY, api_password="router-pw",
        )
        self.staff = User.objects.create_user(
            username="tech", password="pw-for-tests", role=User.Role.TECHNICIAN
        )
        self.admin = User.objects.create_user(
            username="root", password="pw-for-tests", role=User.Role.ADMIN
        )

    def _get(self, user, path):
        self.client.force_authenticate(user)
        res = self.client.get(path)
        self.assertEqual(res.status_code, 200, res.data)
        return res

    def test_the_device_list_does_not_echo_the_snmp_community(self):
        res = self._get(self.staff, "/api/devices/?page_size=100")
        self.assertNotIn(COMMUNITY, str(res.data))

    def test_a_single_device_does_not_echo_the_snmp_community(self):
        res = self._get(self.staff, f"/api/devices/{self.device.id}/")
        self.assertNotIn("snmp_community", res.data)
        self.assertNotIn(COMMUNITY, str(res.data))

    def test_not_even_an_admin_can_read_it_back(self):
        """Same rule as the admin form: this is not a permissions question,
        the value is simply never rendered back."""
        res = self._get(self.admin, f"/api/devices/{self.device.id}/")
        self.assertNotIn(COMMUNITY, str(res.data))

    def test_it_says_whether_one_is_on_file(self):
        """The frontend still needs to show "set"/"not set", the same way
        api_password_set does."""
        res = self._get(self.staff, f"/api/devices/{self.device.id}/")
        self.assertTrue(res.data["snmp_community_set"])

        bare = Device.objects.create(
            name="No SNMP", device_type="switch", ip_address="10.0.0.2", snmp_community="",
        )
        res = self._get(self.staff, f"/api/devices/{bare.id}/")
        self.assertFalse(res.data["snmp_community_set"])

    # ---- it must still be settable --------------------------------------

    def test_the_community_can_still_be_set(self):
        self.client.force_authenticate(self.admin)
        res = self.client.patch(
            f"/api/devices/{self.device.id}/", {"snmp_community": "rotated"}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.device.refresh_from_db()
        self.assertEqual(self.device.snmp_community, "rotated")

    def test_saving_the_form_without_it_keeps_the_stored_one(self):
        """The value is no longer echoed back, so an edit form round-trips
        it as blank. Blank must mean "keep", or the first save of an
        unrelated field would silently break SNMP polling for that box."""
        self.client.force_authenticate(self.admin)
        res = self.client.patch(
            f"/api/devices/{self.device.id}/",
            {"name": "Teraco Core 2", "snmp_community": ""},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.device.refresh_from_db()
        self.assertEqual(self.device.name, "Teraco Core 2")
        self.assertEqual(self.device.snmp_community, COMMUNITY)

    def test_the_api_password_is_still_write_only_too(self):
        """Guarding the pattern this was made to match."""
        res = self._get(self.staff, f"/api/devices/{self.device.id}/")
        self.assertNotIn("api_password", res.data)
        self.assertTrue(res.data["api_password_set"])
