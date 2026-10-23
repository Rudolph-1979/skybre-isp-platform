from django.apps import AppConfig


class ConectoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "conecto"
    verbose_name = "Conecto subscriber app"

    def ready(self):
        # Registers the outbound push signals. Import here rather than at
        # module level so the app registry is fully loaded first.
        from . import signals  # noqa: F401
