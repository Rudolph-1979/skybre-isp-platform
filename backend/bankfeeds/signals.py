"""Keeps a BankTransaction honest when the Payment or Expense it was
confirmed into is later deleted.

Both links are `on_delete=SET_NULL`, so deleting the record used to leave
the transaction sitting at status=confirmed with created_payment /
created_expense set to NULL -- i.e. claiming money had been accounted for
while pointing at nothing. That was unrecoverable through the UI too:
confirm() refuses an already-confirmed transaction and unmatch() refuses
to touch one, so the amount silently vanished from the books (and from
the VAT return) with no way to put it back.

These handlers revert the transaction to the review queue instead, so the
money reappears as something a human still has to deal with. `pre_delete`
rather than `post_delete` because the SET_NULL cascade has already run by
the time post_delete fires -- we need the link while it still exists.
"""
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from .models import BankTransaction


def _revert_to_review_queue(txn):
    """Put a confirmed transaction back where a human will see it. Keeps
    whatever customer/supplier it was matched to, so the reviewer doesn't
    have to redo that work -- only the confirmation itself is undone."""
    if txn is None or txn.status != BankTransaction.Status.CONFIRMED:
        return
    has_match = txn.matched_customer_id or txn.matched_supplier_id
    txn.status = BankTransaction.Status.MATCHED if has_match else BankTransaction.Status.UNMATCHED
    txn.confirmed_by = None
    txn.confirmed_at = None
    txn.save(update_fields=["status", "confirmed_by", "confirmed_at"])


@receiver(pre_delete, sender="expenses.Expense", dispatch_uid="bankfeeds_expense_deleted")
def expense_deleted(sender, instance, **kwargs):
    _revert_to_review_queue(getattr(instance, "bank_transaction", None))


@receiver(pre_delete, sender="billing.Payment", dispatch_uid="bankfeeds_payment_deleted")
def payment_deleted(sender, instance, **kwargs):
    _revert_to_review_queue(getattr(instance, "bank_transaction", None))
