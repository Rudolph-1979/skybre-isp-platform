"""Bank-feed sync engine: pulls new transactions for one BankAccount via
FNBClient, dedupes and matches them (see ingest.py), and always logs a
BankFeedSyncLog row -- used by both the hourly cron (management command,
below) and the "Sync now" button on the Bank Feeds screen. CSV import (see
csv_import.py) is a separate, parallel entry point into the exact same
BankTransaction table for accounts that don't have API access configured
yet -- most accounts, until FNB confirms API access (see fnb_client.py).
"""
from datetime import timedelta

from django.utils import timezone

from .fnb_client import FNBClient, FNBClientError
from .ingest import create_transaction_if_new
from .models import BankAccount, BankFeedSyncLog


def sync_bank_account(account: BankAccount, triggered_by=None) -> BankFeedSyncLog:
    """Fetches transactions since this account's last successful sync (or
    the last 30 days if it's never synced), creates any not already
    stored, and returns a logged BankFeedSyncLog -- on success or failure,
    mirroring run_recurring_billing's "always log it, even on failure"
    convention so a broken account is visible in History, not silently
    stuck."""
    since_date = account.last_synced_at.date() if account.last_synced_at else timezone.localdate() - timedelta(days=30)

    fetched = 0
    created_count = 0
    matched_count = 0
    status = BankFeedSyncLog.Status.SUCCESS
    message = ""

    try:
        client = FNBClient(account)
        raw_transactions = client.fetch_transactions(since_date)
        fetched = len(raw_transactions)
        # Read both lookup tables once per sync, not once per transaction --
        # same reasoning as the CSV import path in views.py.
        from inventory.models import Supplier
        from customers.models import Customer

        suppliers = list(Supplier.objects.all().only("id", "name"))
        customers = list(Customer.objects.exclude(customer_id="").only("id", "customer_id"))
        for raw in raw_transactions:
            _txn, created, matched = create_transaction_if_new(
                account=account,
                source="api",
                external_id=raw["external_id"],
                date=raw["date"],
                description=raw.get("description", ""),
                amount=raw["amount"],
                raw_payload=raw.get("raw", {}),
                suppliers=suppliers,
                customers=customers,
            )
            if created:
                created_count += 1
            if matched:
                matched_count += 1

        account.last_synced_at = timezone.now()
        account.last_sync_status = BankAccount.SyncStatus.SUCCESS
        account.last_sync_message = ""
        account.save(update_fields=["last_synced_at", "last_sync_status", "last_sync_message"])
    except FNBClientError as exc:
        status = BankFeedSyncLog.Status.FAILED
        message = str(exc)
        account.last_sync_status = BankAccount.SyncStatus.FAILED
        account.last_sync_message = message
        account.save(update_fields=["last_sync_status", "last_sync_message"])

    return BankFeedSyncLog.objects.create(
        account=account,
        status=status,
        status_message=message,
        transactions_fetched=fetched,
        transactions_new=created_count,
        transactions_matched=matched_count,
        triggered_by=triggered_by,
    )


def sync_all_active_accounts(triggered_by=None) -> list:
    """Runs sync_bank_account for every active BankAccount -- one
    account's failure (e.g. no API access configured yet) doesn't stop the
    others from syncing. Returns the list of BankFeedSyncLog rows created,
    one per account."""
    logs = []
    for account in BankAccount.objects.filter(is_active=True):
        logs.append(sync_bank_account(account, triggered_by=triggered_by))
    return logs
