from django.apps import AppConfig


class RadiusauthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "radiusauth"
    verbose_name = "RADIUS / OVPN Authentication"

    def ready(self):
        # Registers the Service/Tariff -> radcheck/radreply sync signals.
        from . import signals  # noqa: F401
