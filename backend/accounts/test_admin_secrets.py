"""Django's admin must not render back the secrets the API hides.

The REST API is careful: every credential is write_only with a *_set
boolean so the UI can say whether one is on file without echoing it. The
admin's default ModelForm has no such notion, and three ModelAdmins were
relying on `readonly_fields` to hide values -- which it does not do. In
Django, readonly_fields means "render this as uneditable text". The value
is still printed.

TwoFactorAuthAdmin was the worst of the three, because the comment two
lines above it said "never expose the secret itself here" while the code
did exactly that. Anyone given view access for the stated purpose --
checking who has 2FA on -- could read the base32 TOTP seed, enter it into
an authenticator app, and generate a valid second factor for that account
indefinitely, with nothing about the row changing to show it.
"""
from django.contrib import admin
from django.test import TestCase

from accounts.models import TwoFactorAuth
from billing.models import Service
from network.models import Device


def admin_request():
    """A real request with a superuser.

    ModelAdmin.get_form and the has_*_permission hooks reach for
    request.user, so None will not do. get_or_create because a single test
    calls this more than once and the username is unique.
    """
    from django.contrib.auth import get_user_model
    from django.test import RequestFactory

    User = get_user_model()
    user, created = User.objects.get_or_create(
        username="admin-probe",
        defaults={"email": "a@x.com", "is_staff": True, "is_superuser": True},
    )
    if created:
        user.set_password("pw-for-tests")
        user.save()
    request = RequestFactory().get("/django-admin/")
    request.user = user
    return request


class AdminSecretExposureTests(TestCase):
    def _form_fields(self, model):
        """The fields the admin's change form would actually render."""
        model_admin = admin.site._registry[model]
        request = admin_request()
        # get_form(None) with no obj is what the add/change view does; its
        # base_fields is the definitive answer to "what gets rendered".
        form = model_admin.get_form(request)
        rendered = set(form.base_fields)
        # readonly_fields are rendered too -- as text rather than inputs --
        # so they count as exposed for this purpose. That is the whole
        # point of these tests.
        rendered |= set(model_admin.get_readonly_fields(request))
        return rendered

    def test_the_totp_secret_is_not_on_the_form(self):
        self.assertNotIn(
            "secret", self._form_fields(TwoFactorAuth),
            "the TOTP seed is rendered on the admin change form -- readonly_fields "
            "displays a value, it does not hide it",
        )

    def test_the_router_api_password_is_not_on_the_form(self):
        self.assertNotIn("api_password", self._form_fields(Device))

    def test_the_snmp_community_is_not_on_the_form(self):
        self.assertNotIn("snmp_community", self._form_fields(Device))

    def test_the_subscribers_radius_password_is_not_on_the_form(self):
        self.assertNotIn("radius_password", self._form_fields(Service))

    def test_a_totp_device_cannot_be_created_from_the_admin(self):
        """One made here would have no usable secret, which would lock the
        user out of their own second factor."""
        self.assertFalse(admin.site._registry[TwoFactorAuth].has_add_permission(admin_request()))

    def test_the_fields_that_should_still_be_visible_are(self):
        """The admin is genuinely useful for support -- confirming who has
        2FA on, checking a device's address. Excluding the credentials must
        not gut it."""
        device_fields = self._form_fields(Device)
        for field in ("name", "ip_address", "api_port", "api_enabled"):
            self.assertIn(field, device_fields)
        self.assertIn("radius_username", self._form_fields(Service))


class PaymentAdminStaysLockedTests(TestCase):
    """Regression guard for an earlier fix: every ledger effect of a
    payment lives in the serializer, so the admin's default forms would
    move money in the payments table without moving it in the ledger."""

    def test_payments_cannot_be_added_changed_or_deleted(self):
        from billing.models import Payment

        payment_admin = admin.site._registry[Payment]
        self.assertFalse(payment_admin.has_add_permission(admin_request()))
        self.assertFalse(payment_admin.has_change_permission(admin_request()))
        self.assertFalse(payment_admin.has_delete_permission(admin_request()))
