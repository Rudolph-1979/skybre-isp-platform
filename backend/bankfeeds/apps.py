from django.apps import AppConfig


class BankfeedsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "bankfeeds"

    def ready(self):
        # Registers the pre_delete handlers that stop a deleted Payment/
        # Expense from stranding the BankTransaction it came from -- see
        # signals.py for why this can't be done in the viewset alone.
        from . import signals  # noqa: F401
