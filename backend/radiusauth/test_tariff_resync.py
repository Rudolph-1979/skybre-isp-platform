"""Editing a tariff must only reach the network when the speed changed.

_tariff_post_save_resync_services fired on EVERY non-create Tariff.save().
The handler re-syncs each service's RADIUS rows and then drops its live
session so the router re-reads the rate limit -- correct and necessary
when the speed actually changed, and indefensible when it did not.

Putting a plan's price up, or fixing a typo in its description, therefore
knocked every paid-up customer on that plan offline for a re-dial, with
no RadiusAction row to record that it had happened. On a popular plan
that is a few hundred people at once.

network.signals._service_post_save has always compared old and new before
touching the router; this handler simply never got the same treatment.
"""
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from billing.models import Service, Tariff
from customers.models import Customer


class TariffResyncTests(TestCase):
    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="Home 20", price=Decimal("500.00"),
            speed_download_kbps=20480, speed_upload_kbps=10240,
        )
        self.customer = Customer.objects.create(full_name="Live Larry", email="larry@example.com")
        self.service = Service.objects.create(
            customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date="2026-01-01", radius_username="pppoe_larry",
        )

    def _save_tariff(self, **changes):
        """Save the tariff with `changes` applied, capturing whether the
        network was touched. Patched at the point the handler calls, so
        both halves (row re-sync and the push to the live session) are
        observed."""
        for field, value in changes.items():
            setattr(self.tariff, field, value)
        with mock.patch("radiusauth.signals.sync_service_radius") as sync, \
             mock.patch("network.signals._after_commit_in_background") as pushed:
            self.tariff.save()
        return sync, pushed

    # ---- edits that must NOT reach the network ---------------------------

    def test_a_price_change_does_not_touch_the_network(self):
        sync, pushed = self._save_tariff(price=Decimal("549.00"))
        sync.assert_not_called()
        pushed.assert_not_called()

    def test_a_description_change_does_not_touch_the_network(self):
        sync, pushed = self._save_tariff(description="Now with free installation")
        sync.assert_not_called()
        pushed.assert_not_called()

    def test_deactivating_a_tariff_does_not_disconnect_its_customers(self):
        sync, pushed = self._save_tariff(is_active=False)
        sync.assert_not_called()
        pushed.assert_not_called()

    def test_a_tax_rate_change_does_not_touch_the_network(self):
        sync, pushed = self._save_tariff(tax_rate_pct=Decimal("15.00"))
        sync.assert_not_called()
        pushed.assert_not_called()

    def test_an_unchanged_resave_does_not_touch_the_network(self):
        sync, pushed = self._save_tariff()
        sync.assert_not_called()
        pushed.assert_not_called()

    # ---- edits that MUST reach the network -------------------------------

    def test_a_download_speed_change_resyncs_and_pushes(self):
        sync, pushed = self._save_tariff(speed_download_kbps=51200)
        sync.assert_called_once()
        pushed.assert_called_once()

    def test_an_upload_speed_change_resyncs_and_pushes(self):
        sync, pushed = self._save_tariff(speed_upload_kbps=20480)
        sync.assert_called_once()
        pushed.assert_called_once()

    def test_a_fair_use_threshold_change_resyncs_and_pushes(self):
        sync, pushed = self._save_tariff(fup_threshold_gb=500)
        sync.assert_called_once()
        pushed.assert_called_once()

    def test_a_shaped_speed_change_resyncs_and_pushes(self):
        sync, pushed = self._save_tariff(fup_speed_pct=50)
        sync.assert_called_once()
        pushed.assert_called_once()

    def test_creating_a_tariff_touches_nothing(self):
        with mock.patch("radiusauth.signals.sync_service_radius") as sync:
            Tariff.objects.create(
                name="Brand New", price=Decimal("100.00"),
                speed_download_kbps=10240, speed_upload_kbps=5120,
            )
        sync.assert_not_called()

    def test_a_suspended_service_is_resynced_but_not_pushed(self):
        """A suspended line is on the walled garden and picks the new speed
        up when it is reactivated -- pre-existing behaviour, kept."""
        self.service.status = Service.Status.SUSPENDED
        self.service.save()
        sync, pushed = self._save_tariff(speed_download_kbps=51200)
        sync.assert_called_once()
        pushed.assert_not_called()

    def test_the_speed_change_goes_out_as_coa_without_a_disconnect_fallback(self):
        """A speed change is what CoA handles WITHOUT dropping the customer.
        The fallback is off because this fans out across every customer on
        the plan at once -- the same reasoning apply_change's docstring
        gives for the scheduled speed-policy run.

        The real push is on_commit + a daemon thread, so rather than racing
        that thread the deferral is run inline here: the assertion is about
        what gets asked of the router, not about the threading.
        """
        self.tariff.speed_download_kbps = 51200
        with mock.patch("radiusauth.signals.sync_service_radius"), \
             mock.patch("network.signals._after_commit_in_background",
                        side_effect=lambda description, work: work()), \
             mock.patch("radiusauth.enforcement.apply_change") as apply_change:
            self.tariff.save()
        apply_change.assert_called_once()
        self.assertEqual(apply_change.call_args.args[1], "tariff")
        self.assertIs(apply_change.call_args.kwargs["allow_disconnect_fallback"], False)
