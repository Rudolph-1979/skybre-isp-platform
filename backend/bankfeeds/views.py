from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.permissions import IsAdmin, IsStaffMember, section_permission
from billing.serializers import PaymentSerializer
from expenses.serializers import ExpenseSerializer
from .csv_import import StatementFormatError, parse_statement_csv
from .ingest import create_transaction_if_new
from .models import BankAccount, BankTransaction, BankFeedSyncLog
from .serializers import BankAccountSerializer, BankTransactionSerializer, BankFeedSyncLogSerializer
from .sync import sync_bank_account

# Bank Feeds lives under Accountant now (moved 2026-08-19 from Finance,
# alongside VAT Returns/Expenses -- see expenses/views.py's identical
# HasAccountantAccess) since confirming a transaction here is exactly the
# same underlying action as recording a Payment or Expense by hand: both
# feed straight into the VAT return. Bank ACCOUNT credentials
# (BankAccountViewSet) are further restricted to Admin only, matching
# EmailSettings/OvpnSettings' tier for platform-wide API secrets.
HasAccountantAccess = section_permission("accountant")


class BankAccountViewSet(viewsets.ModelViewSet):
    """CRUD for the (up to 4) FNB accounts this reads from. Creating or
    editing one is Admin-only, since api_client_id/api_client_secret are
    effectively banking API credentials, the same trust tier as SMTP/OVPN
    platform settings."""

    serializer_class = BankAccountSerializer
    queryset = BankAccount.objects.all()

    def get_permissions(self):
        # Listing is open to staff with the Accountant section, not just
        # Admins: the Review tab's "Import statement CSV" dialog needs an
        # account picker, and gating the list on IsAdmin left that
        # dropdown silently empty (and the Preview button permanently
        # disabled) for every non-admin accountant. Safe to widen because
        # BankAccountSerializer never returns api_client_secret -- it's
        # write-only, surfaced only as the boolean api_client_secret_set.
        # Everything that mutates an account, and sync-now (which spends
        # the credentials), stays Admin-only.
        if self.action in ("list", "retrieve"):
            return [permissions.IsAuthenticated(), IsStaffMember(), HasAccountantAccess()]
        return [permissions.IsAuthenticated(), IsAdmin()]

    @action(detail=True, methods=["post"], url_path="sync-now")
    def sync_now(self, request, pk=None):
        account = self.get_object()
        log = sync_bank_account(account, triggered_by=request.user)
        return Response(BankFeedSyncLogSerializer(log).data)


class BankTransactionViewSet(viewsets.ReadOnlyModelViewSet):
    """The Bank Feeds review queue -- list/retrieve plus the specific
    state-transition actions below (assign/confirm/ignore/unmatch). No
    generic create/update/destroy: every change to a transaction's status
    is one of these explicit, auditable actions."""

    serializer_class = BankTransactionSerializer
    queryset = BankTransaction.objects.select_related(
        "account", "matched_customer", "matched_supplier", "confirmed_by"
    ).all()
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasAccountantAccess]
    filterset_fields = ["status", "account", "source"]

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        """Manually set (or change) which customer (credit) or supplier
        (debit) this transaction is attributed to, without confirming it
        yet -- lets staff pick a match from the review queue's dropdown
        and see it before committing to Confirm. A debit transaction can
        skip this entirely and go straight to Confirm with a free-text
        supplier name instead (see confirm() below) -- assign() here is
        only for attaching a known Supplier record."""
        txn = self.get_object()
        if txn.status == BankTransaction.Status.CONFIRMED:
            return Response({"detail": "This transaction is already confirmed -- it can't be reassigned."}, status=400)

        if txn.is_credit:
            from customers.models import Customer

            customer_id = request.data.get("customer")
            if not customer_id:
                return Response({"detail": "A customer is required."}, status=400)
            txn.matched_customer = get_object_or_404(Customer, pk=customer_id)
            txn.match_method = BankTransaction.MatchMethod.MANUAL
            txn.status = BankTransaction.Status.MATCHED
            txn.save(update_fields=["matched_customer", "match_method", "status"])
        else:
            from inventory.models import Supplier

            supplier_id = request.data.get("supplier")
            if not supplier_id:
                return Response({"detail": "A supplier is required."}, status=400)
            txn.matched_supplier = get_object_or_404(Supplier, pk=supplier_id)
            txn.match_method = BankTransaction.MatchMethod.MANUAL
            txn.status = BankTransaction.Status.MATCHED
            txn.save(update_fields=["matched_supplier", "match_method", "status"])
        return Response(BankTransactionSerializer(txn).data)

    @action(detail=True, methods=["post"])
    def unmatch(self, request, pk=None):
        """Reverts a not-yet-confirmed match back to unmatched -- undoes
        an assign() (manual or auto reference/name-match) that turned out
        to be wrong, before it becomes a real Payment or Expense."""
        txn = self.get_object()
        if txn.status == BankTransaction.Status.CONFIRMED:
            return Response({"detail": "This transaction is already confirmed -- it can't be unmatched."}, status=400)

        txn.matched_customer = None
        txn.matched_supplier = None
        txn.match_method = ""
        txn.status = BankTransaction.Status.UNMATCHED
        txn.save(update_fields=["matched_customer", "matched_supplier", "match_method", "status"])
        return Response(BankTransactionSerializer(txn).data)

    @action(detail=True, methods=["post"])
    def ignore(self, request, pk=None):
        """Marks a transaction as not relevant to customer payments or
        business expenses (e.g. an internal transfer, or a bank fee not
        worth tracking as an Expense) -- removes it from the review queue
        without creating a Payment or Expense."""
        txn = self.get_object()
        if txn.status == BankTransaction.Status.CONFIRMED:
            return Response({"detail": "This transaction is already confirmed -- it can't be ignored."}, status=400)

        txn.status = BankTransaction.Status.IGNORED
        txn.save(update_fields=["status"])
        return Response(BankTransactionSerializer(txn).data)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        """The one action that actually creates a real accounting record
        -- a billing.Payment for a credit, an expenses.Expense for a
        debit. Every match (auto or manual) sits here until a staff
        member explicitly confirms it, per this feature's design: nothing
        posts automatically, for either direction. See
        _confirm_as_payment / _confirm_as_expense below for the two
        branches.

        The whole thing runs in one transaction, with the BankTransaction
        row locked FOR UPDATE first. Both matter: without the lock, two
        staff members (or a double-clicked button) could pass the
        already-confirmed check at the same time and post the same money
        twice; without the atomic block, a failure between "Payment/
        Expense created" and "transaction marked confirmed" would leave a
        real accounting record with nothing pointing at it.
        """
        with transaction.atomic():
            txn = BankTransaction.objects.select_for_update().get(pk=self.get_object().pk)
            if txn.status == BankTransaction.Status.CONFIRMED:
                return Response({"detail": "This transaction has already been confirmed."}, status=400)
            if txn.status == BankTransaction.Status.IGNORED:
                return Response({"detail": "This transaction is ignored -- unmatch it first if it should be posted after all."}, status=400)

            if txn.is_credit:
                return self._confirm_as_payment(request, txn)
            return self._confirm_as_expense(request, txn)

    def _confirm_as_payment(self, request, txn):
        """Credit side -- unchanged from the original income-only design.
        Accepts an optional `customer` (to assign-and-confirm in one
        step) and an optional `invoice` (to apply the payment against a
        specific invoice, exactly like a normal manual Payment).

        Judgment call: the resulting Payment.date is auto_now_add (set to
        the moment of confirmation, same as every other Payment in this
        app), NOT the bank's own transaction date -- that stays on this
        BankTransaction row (linked via created_payment) as the source of
        truth for when the money actually arrived.
        """
        customer_id = request.data.get("customer")
        if customer_id:
            from customers.models import Customer

            txn.matched_customer = get_object_or_404(Customer, pk=customer_id)
            txn.match_method = BankTransaction.MatchMethod.MANUAL

        if not txn.matched_customer:
            return Response({"detail": "Assign a customer to this transaction before confirming."}, status=400)

        payload = {
            "customer": txn.matched_customer_id,
            "amount": str(txn.amount),
            "method": "bank_transfer",
            "note": f"Bank feed: {txn.description}"[:255],
        }
        invoice_id = request.data.get("invoice")
        if invoice_id:
            payload["invoice"] = invoice_id

        serializer = PaymentSerializer(data=payload, context={"request": request})
        serializer.is_valid(raise_exception=True)
        payment = serializer.save()

        txn.created_payment = payment
        txn.confirmed_by = request.user
        txn.confirmed_at = timezone.now()
        txn.status = BankTransaction.Status.CONFIRMED
        txn.save(update_fields=["matched_customer", "match_method", "created_payment", "confirmed_by", "confirmed_at", "status"])
        return Response(BankTransactionSerializer(txn).data)

    def _confirm_as_expense(self, request, txn):
        """Debit side. Since a bank transaction only ever carries a date,
        description and total amount, staff fill in the rest at
        confirm-time -- category, an optional supplier (a known Supplier
        record, or a free-text `supplier_name` if it doesn't match one),
        and the VAT rate to back out of the total.

        Judgment call: `amount` on a BankTransaction is money actually
        paid, i.e. VAT-inclusive -- so amount_excl_vat is computed
        backward from it using the given (or default 15%) vat_rate_pct,
        the same direction every other VAT figure in this app works
        (Expense.vat_amount/amount_incl_vat go the other way, from a
        VAT-exclusive amount entered by hand). `date` is always the
        transaction's own date -- there's no separate supplier-invoice
        date available from a bank feed row.
        """
        from expenses.models import Expense

        supplier_id = request.data.get("supplier")
        if supplier_id:
            from inventory.models import Supplier

            txn.matched_supplier = get_object_or_404(Supplier, pk=supplier_id)
            txn.match_method = BankTransaction.MatchMethod.MANUAL

        supplier_name = request.data.get("supplier_name", "")
        if not txn.matched_supplier and not supplier_name:
            return Response(
                {"detail": "Assign a supplier (or type a supplier name) before confirming."}, status=400
            )

        # vat_rate_pct is required and must be sane. It used to default to
        # "15" silently, which was two separate problems: a rate of exactly
        # -100 made the divisor below zero (an uncaught DivisionByZero ->
        # HTTP 500), and -- worse in practice -- click-through confirming a
        # salary run, an inter-account transfer or a loan repayment at a
        # default 15% would claim Input VAT that was never charged. SARS
        # penalises overclaimed Input VAT, so the rate is now an explicit
        # decision on every debit rather than a default worth trusting.
        raw_rate = request.data.get("vat_rate_pct", None)
        if raw_rate is None or str(raw_rate).strip() == "":
            return Response(
                {"detail": "A VAT rate is required -- use 0 for a purchase that carries no VAT (salaries, transfers, loan repayments)."},
                status=400,
            )
        try:
            vat_rate_pct = Decimal(str(raw_rate))
        except (InvalidOperation, ValueError):
            return Response({"detail": "vat_rate_pct must be a number."}, status=400)
        if not vat_rate_pct.is_finite():
            return Response({"detail": "vat_rate_pct must be a finite number."}, status=400)
        if vat_rate_pct < 0 or vat_rate_pct > 100:
            return Response({"detail": "vat_rate_pct must be between 0 and 100."}, status=400)

        total_incl_vat = abs(txn.amount)
        amount_excl_vat = (total_incl_vat / (Decimal("1") + vat_rate_pct / Decimal("100"))).quantize(Decimal("0.01"))

        payload = {
            "supplier": txn.matched_supplier_id,
            "supplier_name": "" if txn.matched_supplier_id else supplier_name,
            "category": request.data.get("category", Expense.Category.OTHER),
            "description": request.data.get("description") or txn.description or f"Bank feed expense ({txn.date})",
            "invoice_number": request.data.get("invoice_number", ""),
            "date": str(txn.date),
            "amount_excl_vat": str(amount_excl_vat),
            "vat_rate_pct": str(vat_rate_pct),
            "notes": f"Bank feed: {txn.description}",
        }

        serializer = ExpenseSerializer(data=payload, context={"request": request})
        serializer.is_valid(raise_exception=True)
        expense = serializer.save(created_by=request.user)

        txn.created_expense = expense
        txn.confirmed_by = request.user
        txn.confirmed_at = timezone.now()
        txn.status = BankTransaction.Status.CONFIRMED
        txn.save(update_fields=["matched_supplier", "match_method", "created_expense", "confirmed_by", "confirmed_at", "status"])
        return Response(BankTransactionSerializer(txn).data)

    @action(detail=False, methods=["post"], url_path="import-preview")
    def import_preview(self, request):
        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response({"detail": "No file uploaded (expected form field 'file')."}, status=400)
        account_id = request.data.get("account")
        if not account_id:
            return Response({"detail": "Select which bank account this statement is for."}, status=400)
        account = get_object_or_404(BankAccount, pk=account_id)

        try:
            rows = parse_statement_csv(file_obj)
        except StatementFormatError as exc:
            # A whole-file problem (no header row found), not per-row errors --
            # returned as a 400 with the real reason so the modal can show it
            # instead of a generic "couldn't read that file".
            return Response({"detail": str(exc)}, status=400)
        existing_ids = set(
            BankTransaction.objects.filter(
                account=account, external_id__in=[r["external_id"] for r in rows if r["external_id"]]
            ).values_list("external_id", flat=True)
        )
        for r in rows:
            r["already_imported"] = bool(r["external_id"]) and r["external_id"] in existing_ids

        valid = [r for r in rows if not r["errors"]]
        return Response(
            {
                "total_rows": len(rows),
                "valid_count": len(valid),
                "invalid_count": len(rows) - len(valid),
                "already_imported_count": sum(1 for r in rows if r["already_imported"]),
                "rows": rows,
            }
        )

    @action(detail=False, methods=["post"], url_path="import-commit")
    def import_commit(self, request):
        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response({"detail": "No file uploaded (expected form field 'file')."}, status=400)
        account_id = request.data.get("account")
        if not account_id:
            return Response({"detail": "Select which bank account this statement is for."}, status=400)
        account = get_object_or_404(BankAccount, pk=account_id)

        try:
            rows = parse_statement_csv(file_obj)
        except StatementFormatError as exc:
            return Response({"detail": str(exc)}, status=400)
        # Read the supplier and customer lists once for the whole import
        # rather than letting every row re-query the entire table.
        from inventory.models import Supplier
        from customers.models import Customer

        suppliers = list(Supplier.objects.all().only("id", "name"))
        # Reference matching now compares against every customer's own
        # reference -- they are no longer a fixed CUS-###### pattern the
        # matcher could reconstruct and look up directly -- so without this
        # prefetch a credit row would cost a query each.
        customers = list(Customer.objects.exclude(customer_id="").only("id", "customer_id"))
        created_count = 0
        duplicate_count = 0
        matched_count = 0
        skipped = []
        for r in rows:
            if r["errors"]:
                skipped.append({"row": r["row"], "errors": r["errors"]})
                continue
            try:
                amount = Decimal(r["amount"])
            except InvalidOperation:
                skipped.append({"row": r["row"], "errors": [f"'{r['amount']}' isn't a valid amount"]})
                continue
            _txn, created, matched = create_transaction_if_new(
                account=account, source=BankTransaction.Source.CSV_IMPORT, external_id=r["external_id"],
                date=r["date"], description=r["description"], amount=amount,
                suppliers=suppliers, customers=customers,
            )
            if created:
                created_count += 1
                if matched:
                    matched_count += 1
            else:
                duplicate_count += 1

        return Response(
            {
                "created": created_count,
                "duplicates_skipped": duplicate_count,
                "matched": matched_count,
                "invalid_skipped": len(skipped),
                "skipped": skipped,
            }
        )


class BankFeedSyncLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only History for the Bank Feeds screen -- rows are only ever
    created by sync_bank_account (via the hourly cron or "Sync now")."""

    serializer_class = BankFeedSyncLogSerializer
    queryset = BankFeedSyncLog.objects.select_related("account", "triggered_by").all()
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasAccountantAccess]
    filterset_fields = ["account", "status"]
