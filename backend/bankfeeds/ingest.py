"""Shared "create this transaction if it's new, then try to match it"
logic -- used by both sync.py (API-sourced rows) and views.py's CSV import
commit action (CSV-sourced rows), so the matching behavior can't drift
between the two entry points.
"""
from datetime import date as date_cls
from decimal import Decimal

from .matching import match_customer_by_reference, match_supplier_by_name
from .models import BankTransaction


def create_transaction_if_new(
    account, source, external_id, date, description, amount, raw_payload=None,
    suppliers=None, customers=None,
):
    """Returns (transaction, created, matched). If a transaction for this
    (account, external_id) already exists, returns it unchanged (created
    and matched both False) -- this is the dedupe guard that makes
    re-syncing or re-uploading the same statement twice a safe no-op.

    A new transaction with amount == 0 is ignored outright -- there's
    nothing to allocate either way. Anything else lands in the review
    queue: a credit (money in) tries to auto-match a Customer by
    reference number; a debit (money out) tries to auto-match a Supplier
    by name. Either way, matching only ever suggests -- nothing becomes a
    real Payment or Expense until a staff member explicitly confirms it
    (see BankTransactionViewSet.confirm).

    `suppliers` and `customers` are optional pre-fetched lists, passed
    straight through to the matchers so a bulk CSV import reads each table
    once rather than once per row.
    """
    if isinstance(date, str):
        date = date_cls.fromisoformat(date)
    if isinstance(amount, str):
        amount = Decimal(amount)

    defaults = {
        "date": date,
        "description": description or "",
        "amount": amount,
        "source": source,
        "raw_payload": raw_payload or {},
    }
    if amount == 0:
        defaults["status"] = BankTransaction.Status.IGNORED

    txn, created = BankTransaction.objects.get_or_create(
        account=account, external_id=external_id, defaults=defaults
    )
    if not created:
        return txn, False, False

    matched = False
    if txn.status == BankTransaction.Status.UNMATCHED:
        if txn.is_credit:
            customer = match_customer_by_reference(txn.description, customers=customers)
            if customer is not None:
                txn.matched_customer = customer
                txn.match_method = BankTransaction.MatchMethod.REFERENCE
                txn.status = BankTransaction.Status.MATCHED
                txn.save(update_fields=["matched_customer", "match_method", "status"])
                matched = True
        else:
            supplier = match_supplier_by_name(txn.description, suppliers=suppliers)
            if supplier is not None:
                txn.matched_supplier = supplier
                txn.match_method = BankTransaction.MatchMethod.REFERENCE
                txn.status = BankTransaction.Status.MATCHED
                txn.save(update_fields=["matched_supplier", "match_method", "status"])
                matched = True
    return txn, True, matched
