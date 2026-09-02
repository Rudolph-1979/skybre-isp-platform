from django.apps import AppConfig


class CustomersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "customers"

    def ready(self):
        # Registers the status propagation in both directions between
        # Customer and Service.
        from . import signals  # noqa: F401
