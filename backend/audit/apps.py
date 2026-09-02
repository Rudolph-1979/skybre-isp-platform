from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "audit"
    verbose_name = "Audit trail"

    def ready(self):
        # Importing the module connects its signal receivers. Everything
        # tracked is registered there; nothing else in the project has to
        # know this app exists.
        from . import tracking  # noqa: F401
