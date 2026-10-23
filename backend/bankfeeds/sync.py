"""Bank-feed sync engine: pulls new transactions for one BankAccount via
FNBClient, dedupes and matches them (see ingest.py), and always logs a
BankFeedSyncLog row -- used by both the hourly cron (management command,
below) and the "Sync now" button on the Bank Feeds screen. CSV import (see
csv_import.py) is a separate, parallel entry point into the exact same
BankTransaction table for accounts that don't have API access configured
yet -- most accounts, until FNB confirms API access (see fnb_client.py).
"""
import logging
from datetime import timedelta

from django.utils import timezone

from .fnb_client import FNBClient, FNBClientError
from .ingest import create_transaction_if_new
from .models import BankAccount, BankFeedSyncLog

logger = logging.getLogger(__name__)


# Days re-read on every sync, so a line FNB books a day or two late is
# still picked up; re-reading is harmless (see ingest's dedupe).
OVERLAP_DAYS = 3
FIRST_SYNC_DAYS = 30


def sync_bank_account(account: BankAccount, triggered_by=None) -> BankFeedSyncLog:
    """Fetches transactions since this account's last successful sync
    (less a few days' overlap; the last 30 days if it's never synced),
    creates any not already stored, and returns a logged BankFeedSyncLog
    -- on success or failure -- so a broken account is visible in History,
    not silently stuck.

    Any error is caught and logged on this account, never raised: one bad
    account (or one unreadable answer) must not stop the others.
    """
    since_date = (
        timezone.localdate(account.last_synced_at) - timedelta(days=OVERLAP_DAYS)
        if account.last_synced_at
        else timezone.localdate() - timedelta(days=FIRST_SYNC_DAYS)
    )

    fetched = 0
    created_count = 0
    matched_count = 0
    twinned = 0
    status = BankFeedSyncLog.Status.SUCCESS
    message = ""

    try:
        client = FNBClient(account)
        result = client.fetch_transactions(since_date)
        raw_transactions = result["transactions"]
        fetched = len(raw_transactions)
        # Read both lookup tables once per sync, not once per transaction --
        # same reasoning as the CSV import path in views.py.
        from inventory.models import Supplier
        from customers.models import Customer

        suppliers = list(Supplier.objects.all().only("id", "name"))
        customers = list(Customer.objects.exclude(customer_id="").only("id", "customer_id"))
        for raw in raw_transactions:
            txn, created, matched = create_transaction_if_new(
                account=account,
                source="api",
                external_id=raw["external_id"],
                date=raw["date"],
                description=raw.get("description", ""),
                reference=raw.get("reference", ""),
                amount=raw["amount"],
                raw_payload=raw.get("raw", {}),
                suppliers=suppliers,
                customers=customers,
            )
            if created:
                created_count += 1
                if txn.duplicate_of_id:
                    twinned += 1
            if matched:
                matched_count += 1

        notes = []
        if twinned:
            notes.append(f"{twinned} already on file from a CSV statement (kept as duplicates, not counted twice)")
        if result["skipped"]:
            shown = "; ".join(result["skipped"][:5])
            more = f" (+{len(result['skipped']) - 5} more)" if len(result["skipped"]) > 5 else ""
            notes.append(f"{len(result['skipped'])} line(s) couldn't be read and were skipped: {shown}{more}")
        message = ". ".join(notes)

        account.last_synced_at = timezone.now()
        account.last_sync_status = BankAccount.SyncStatus.SUCCESS
        account.last_sync_message = message
        fields = ["last_synced_at", "last_sync_status", "last_sync_message"]
        if result.get("balance") is not None:
            account.bank_balance = result["balance"]
            account.bank_balance_at = account.last_synced_at
            fields += ["bank_balance", "bank_balance_at"]
        account.save(update_fields=fields)
    except Exception as exc:  # noqa: BLE001 -- logged on the account, see docstring
        status = BankFeedSyncLog.Status.FAILED
        if isinstance(exc, FNBClientError):
            message = str(exc)
        else:
            logger.exception("Bank feed sync of %s failed", account)
            message = f"Unexpected error while syncing ({type(exc).__name__}): {exc}"
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
    """Runs sync_bank_account for every active account that HAS an API
    connection set up -- CSV-only accounts are skipped rather than logging
    a "use CSV import" failure every hour. Returns the BankFeedSyncLog
    rows created, one per account synced."""
    logs = []
    for account in BankAccount.objects.filter(is_active=True).exclude(api_base_url=""):
        logs.append(sync_bank_account(account, triggered_by=triggered_by))
    return logs
