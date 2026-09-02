from django.apps import AppConfig


class NetworkConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "network"

    def ready(self):
        # Registers the Service -> router blocking-rules sync signal.
        from . import signals  # noqa: F401
