from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "billing"

    def ready(self):
        # Registers the Payment handler that reconnects a customer whose
        # balance has just come clear. Imported here rather than at module
        # level because the handler touches models, and nothing may touch
        # models while the app registry is still loading.
        from . import signals  # noqa: F401
