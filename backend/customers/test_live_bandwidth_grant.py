"""The live-speed grant has to be re-enableable.

expire_live_bandwidth_if_idle cleared live_bandwidth_public but left
live_bandwidth_last_viewed_at at its old value -- while
CustomerViewSet.perform_update starts the idle clock only `if
live_bandwidth_public and not live_bandwidth_last_viewed_at`.

So the SECOND time staff turned the switch on, the clock was never
restarted, the stale timestamp was already past the timeout, and the very
next read of the flag -- the retrieve() immediately after the PATCH --
expired it again. The toggle bounced straight back to Off with no
explanation, permanently, for every customer it had ever been used on.
And the field is read-only in the serializer, so there was no way to
clear it through the API either.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from customers.models import Customer

User = get_user_model()


class LiveBandwidthGrantTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Watched Wanda", email="wanda@example.com"
        )

    def _expire(self):
        """Push the last view past the idle timeout and evaluate."""
        stale = timezone.now() - Customer.LIVE_BANDWIDTH_IDLE_TIMEOUT - timedelta(minutes=1)
        Customer.objects.filter(pk=self.customer.pk).update(live_bandwidth_last_viewed_at=stale)
        self.customer.refresh_from_db()
        return self.customer.expire_live_bandwidth_if_idle()

    def test_an_idle_grant_expires(self):
        self.customer.live_bandwidth_public = True
        self.customer.save()
        self.assertTrue(self._expire())
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.live_bandwidth_public)

    def test_expiring_clears_the_clock(self):
        """The fix. Leaving the timestamp behind is what made the switch
        un-re-enableable."""
        self.customer.live_bandwidth_public = True
        self.customer.save()
        self._expire()
        self.customer.refresh_from_db()
        self.assertIsNone(
            self.customer.live_bandwidth_last_viewed_at,
            "the idle clock survived the expiry, so re-enabling will expire instantly",
        )

    def test_a_grant_can_be_re_enabled_after_expiring(self):
        """The end-to-end symptom, through the API."""
        client = APIClient()
        staff = User.objects.create_user(
            username="support9", password="pw-for-tests", role=User.Role.SUPPORT
        )
        client.force_authenticate(staff)

        # First grant, then let it expire.
        res = client.patch(
            f"/api/customers/{self.customer.pk}/", {"live_bandwidth_public": True}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)
        self._expire()
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.live_bandwidth_public)

        # Second grant -- this is the one that used to bounce straight back.
        res = client.patch(
            f"/api/customers/{self.customer.pk}/", {"live_bandwidth_public": True}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)
        # The retrieve() the UI does next is what re-evaluated it.
        res = client.get(f"/api/customers/{self.customer.pk}/")
        self.assertTrue(
            res.data["live_bandwidth_public"],
            "the toggle turned itself back off immediately after being re-enabled",
        )

    def test_a_freshly_enabled_grant_is_not_expired_on_the_spot(self):
        """The `last is None` branch: no record of a view is not the same
        as five idle minutes."""
        self.customer.live_bandwidth_public = True
        self.customer.live_bandwidth_last_viewed_at = None
        self.customer.save()
        self.assertFalse(self.customer.expire_live_bandwidth_if_idle())
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.live_bandwidth_public)
        self.assertIsNotNone(self.customer.live_bandwidth_last_viewed_at)

    def test_a_recently_viewed_grant_survives(self):
        self.customer.live_bandwidth_public = True
        self.customer.save()
        self.customer.touch_live_bandwidth_view()
        self.assertFalse(self.customer.expire_live_bandwidth_if_idle())
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.live_bandwidth_public)

    def test_expiry_is_a_no_op_when_the_switch_is_already_off(self):
        self.assertFalse(self.customer.expire_live_bandwidth_if_idle())
