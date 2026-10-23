"""Bank feed integration: reads incoming payments from up to a handful of
bank accounts (initially 4 FNB accounts) and helps staff match them to the
customer who paid, so a bank transfer doesn't need to be manually copied
into a Payment one line at a time.

Real-time/API access to FNB's transaction data is partner-gated in South
Africa (there's no open-banking mandate here) and this platform doesn't
have that access confirmed/configured yet -- see fnb_client.py's docstring
for exactly what's a placeholder vs. what's real. To make this feature
useful from day one regardless of when (or whether) that API access comes
through, transactions can also be imported from a CSV export of a bank
statement (see views.BankTransactionImportView) -- both sources feed the
exact same BankTransaction table and review workflow below.

Nothing here ever creates a Payment automatically. Every transaction,
however it was matched, sits in "matched" status until a staff member
reviews and confirms it (see BankTransaction.status) -- see the design
discussion this was built from: automatic posting was deliberately ruled
out in favour of a human always confirming the match first.
"""
from django.conf import settings
from django.db import models


class BankAccount(models.Model):
    """One of the (currently up to 4) FNB accounts staff want payments read
    from. API credentials are stored the same way as EmailSettings'
    smtp_password / RADIUS's radius_password -- plain text (the raw value
    has to be sent to the bank's API to authenticate, same as an SMTP
    password), write-only over the API, never echoed back (see
    BankAccountSerializer). api_base_url/api_client_id/api_client_secret
    are deliberately generic (client-credentials-style OAuth2 is the most
    common shape for this kind of API) since the exact FNB endpoint/auth
    flow isn't confirmed yet -- see fnb_client.py.
    """

    name = models.CharField(max_length=100, help_text='e.g. "FNB Cheque - Head Office"')
    account_number = models.CharField(max_length=30, blank=True)
    branch_code = models.CharField(max_length=10, blank=True, help_text="e.g. 250655 for FNB.")
    is_active = models.BooleanField(default=True)

    # -- API connection (placeholder shape -- see fnb_client.py) ----------
    api_base_url = models.CharField(
        max_length=255, blank=True,
        help_text="Base URL for FNB's transaction API once access is confirmed. Leave blank if only using CSV import.",
    )
    api_client_id = models.CharField(max_length=255, blank=True)
    api_client_secret = models.CharField(max_length=255, blank=True)

    # -- FNB Integration Channel: OAuth 2.0 authorisation code (#185) -----
    # FNB's Transaction History API page (read 16 Oct 2026): client ID +
    # secret, JWT-signed client authentication, OAuth 2.0 AUTHORISATION
    # CODE flow (the code arrives at our redirect URL), access token +
    # refresh token; when the refresh token expires the connection has to
    # be made again on Integration Channel. Exact endpoint paths come with
    # FNB's private Swagger document -- see fnb_client.py.
    api_token_url = models.CharField(
        max_length=255, blank=True,
        help_text="FNB's OAuth 2.0 token endpoint (from their spec). Blank = API base URL + /oauth/token.",
    )
    api_authorize_url = models.CharField(
        max_length=255, blank=True,
        help_text="FNB's authorisation page, if they give one. Enables the Connect to FNB button; "
                  "otherwise paste the auth code FNB gives you.",
    )
    api_scope = models.CharField(max_length=255, blank=True)

    class ClientAuth(models.TextChoices):
        POST = "post", "Client ID + secret in the request body"
        BASIC = "basic", "Client ID + secret as HTTP Basic"
        JWT = "jwt", "JWT signed with the client secret (client_secret_jwt)"

    api_client_auth = models.CharField(max_length=10, choices=ClientAuth.choices, default=ClientAuth.POST)

    class Grant(models.TextChoices):
        # What FNB's own Transaction History page describes.
        AUTHORIZATION_CODE = "authorization_code", "Authorisation code (FNB Integration Channel)"
        # Kept as a fallback in case FNB's spec says otherwise for
        # "connect on my own behalf": the client ID + secret alone get a token.
        CLIENT_CREDENTIALS = "client_credentials", "Client credentials (ID + secret only)"

    api_grant = models.CharField(max_length=20, choices=Grant.choices, default=Grant.AUTHORIZATION_CODE)
    api_account_id = models.CharField(
        max_length=64, blank=True,
        help_text="FNB's own identifier for this account in the API, if it isn't the account number.",
    )
    # Tokens are encrypted at rest (bankfeeds/tokens.py, key derived from
    # SECRET_KEY) and never leave the server: not in the API, not in logs.
    api_access_token_enc = models.TextField(blank=True)
    api_refresh_token_enc = models.TextField(blank=True)
    api_token_expires_at = models.DateTimeField(null=True, blank=True)
    api_connected_at = models.DateTimeField(null=True, blank=True)
    api_connected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    # Set when FNB refuses the refresh token: somebody has to connect again
    # on Integration Channel. Cleared by a successful connect.
    api_needs_reconnect = models.BooleanField(default=False)
    # The balance FNB itself reported on the last successful sync.
    bank_balance = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    bank_balance_at = models.DateTimeField(null=True, blank=True)

    # --- balance ----------------------------------------------------------
    # What the account held before the first transaction below, and the
    # date that was true. The running balance is this plus every
    # transaction on or after that date.
    #
    # Built from the BANK LINES, not from payments and expenses. Those are
    # different things and only one of them is the bank: a card payment
    # recorded against a customer, a transfer between two of these
    # accounts, a bank charge nobody has captured yet -- each moves the
    # real balance, and a figure summed from payments and expenses would
    # miss some and double others. The transactions ARE the statement.
    #
    # The date is separate and not optional in practice: an opening
    # balance with no date cannot be placed, and guessing wrong adds a
    # month of transactions to a balance that already included them. With
    # no date, no balance is shown at all -- a blank is better than a
    # number that is confidently a month out.
    opening_balance = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text="The balance on this account immediately before the first transaction on the feed.",
    )
    opening_balance_date = models.DateField(
        null=True, blank=True,
        help_text=(
            "The date that opening balance was true. Transactions on or after it are added to "
            "it. Without a date no running balance is shown."
        ),
    )
    # What internet banking actually said, the last time a person looked.
    # Kept so the page can show the DIFFERENCE rather than just the
    # platform's own figure -- when the two disagree that gap is the
    # useful part, because it means a statement is missing, imported
    # twice, or the opening balance is dated wrong.
    confirmed_balance = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
    )
    confirmed_balance_at = models.DateTimeField(null=True, blank=True)
    confirmed_balance_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )

    last_synced_at = models.DateTimeField(null=True, blank=True)

    class SyncStatus(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    last_sync_status = models.CharField(max_length=10, choices=SyncStatus.choices, blank=True)
    last_sync_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def api_client_secret_set(self):
        return bool(self.api_client_secret)

    @property
    def connection_status(self):
        """csv_only / not_connected / connected / reconnect (#185)."""
        if not self.api_base_url:
            return "csv_only"
        if self.api_needs_reconnect:
            return "reconnect"
        if self.api_grant == self.Grant.CLIENT_CREDENTIALS:
            return "connected" if (self.api_client_id and self.api_client_secret) else "not_connected"
        if self.api_refresh_token_enc or self.api_access_token_enc:
            return "connected"
        return "not_connected"

    def balance_as_at(self, on_date=None):
        """Opening balance plus every transaction on or after the opening
        date, up to `on_date` (default: everything on file).

        None when no opening date has been set -- see the field's comment
        for why a dateless opening balance is worse than no balance.

        IGNORED status is deliberately still counted. "Ignored" means
        "this is not a customer payment I need to match", not "this did
        not happen": a bank charge marked ignored still left the account.
        Excluding them would make the balance drift by exactly the amount
        of everything somebody dismissed.
        """
        from decimal import Decimal

        if self.opening_balance_date is None:
            return None
        # Lines marked as the same transaction already on file from the
        # other source (CSV vs FNB API, #185) are counted once.
        rows = self.transactions.filter(date__gte=self.opening_balance_date, duplicate_of__isnull=True)
        if on_date is not None:
            rows = rows.filter(date__lte=on_date)
        moved = rows.aggregate(total=models.Sum("amount"))["total"] or Decimal("0")
        return (self.opening_balance or Decimal("0")) + moved

    @property
    def current_balance(self):
        return self.balance_as_at()

    @property
    def balance_variance(self):
        """Calculated balance minus the balance somebody last confirmed by
        eye. None when either is missing. Zero means the two agreed; any
        other number is a transaction missing from, or duplicated on, the
        feed -- which is the whole reason for recording a confirmation."""
        calculated = self.current_balance
        if calculated is None or self.confirmed_balance is None:
            return None
        return calculated - self.confirmed_balance


class BankTransaction(models.Model):
    """One line from a bank account's transaction history -- either
    fetched from FNB's API (source=API) or imported from a statement CSV
    export (source=CSV_IMPORT). `amount` is signed: positive is money
    coming in (a customer payment, potentially) and negative is money
    going out (a business expense/purchase, potentially). Both directions
    go through the same review-then-confirm workflow: a credit can be
    matched to a Customer and confirmed into a billing.Payment; a debit
    can be matched to a Supplier (or left free-text) and confirmed into
    an expenses.Expense, so Input VAT can be sourced straight from the
    bank feed too. Neither direction ever posts automatically -- see
    BankTransactionViewSet.confirm.
    """

    class Source(models.TextChoices):
        API = "api", "Bank API"
        CSV_IMPORT = "csv_import", "CSV import"

    class Status(models.TextChoices):
        UNMATCHED = "unmatched", "Unmatched"
        MATCHED = "matched", "Matched (awaiting confirmation)"
        CONFIRMED = "confirmed", "Confirmed"
        IGNORED = "ignored", "Ignored"

    class MatchMethod(models.TextChoices):
        REFERENCE = "reference", "Reference number"
        MANUAL = "manual", "Manually assigned"

    account = models.ForeignKey(BankAccount, on_delete=models.CASCADE, related_name="transactions")
    source = models.CharField(max_length=10, choices=Source.choices)
    # Dedupe key -- the bank's own transaction id for API-sourced rows, or a
    # deterministic hash of (account, date, description, amount, row
    # position in the file) for CSV-sourced ones, so re-importing/re-syncing
    # the same data twice never creates duplicate rows. Never blank.
    external_id = models.CharField(max_length=255)

    date = models.DateField()
    description = models.CharField(max_length=500, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, help_text="Positive = money in, negative = money out.")
    # FNB's API gives the payment reference separately from the details
    # (#185); CSV rows keep theirs in the description.
    reference = models.CharField(max_length=255, blank=True)
    # The same bank line already on file from the OTHER source (a CSV
    # statement vs the FNB API, #185). Such a row is kept for the record
    # but ignored: not counted in the balance, never confirmed.
    duplicate_of = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="duplicates",
    )

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UNMATCHED)
    matched_customer = models.ForeignKey(
        "customers.Customer", on_delete=models.SET_NULL, null=True, blank=True, related_name="bank_transactions"
    )
    # The debit-side counterpart to matched_customer -- an optional,
    # suggested/manually-picked Supplier for a money-out transaction. Not
    # required even to confirm: a debit can still be turned into an
    # Expense with just a free-text supplier name, same as creating an
    # Expense by hand (see ExpenseSerializer's supplier/supplier_name
    # duality) -- most bank descriptions won't match an existing Supplier
    # record exactly.
    matched_supplier = models.ForeignKey(
        "inventory.Supplier", on_delete=models.SET_NULL, null=True, blank=True, related_name="bank_transactions"
    )
    match_method = models.CharField(max_length=10, choices=MatchMethod.choices, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # Set once a staff member confirms a match -- the actual billing.Payment
    # this transaction became. OneToOne: a transaction can back at most one
    # Payment (see BankTransactionViewSet.confirm).
    created_payment = models.OneToOneField(
        "billing.Payment", on_delete=models.SET_NULL, null=True, blank=True, related_name="bank_transaction"
    )
    # The debit-side counterpart to created_payment -- set once a
    # money-out transaction is confirmed into a real expenses.Expense.
    # A transaction can only ever end up with one of created_payment /
    # created_expense set, never both (a row is either a credit or a
    # debit, and confirm() only takes the matching path for its sign).
    created_expense = models.OneToOneField(
        "expenses.Expense", on_delete=models.SET_NULL, null=True, blank=True, related_name="bank_transaction"
    )
    # Raw API response for this line, kept for audit/debugging -- empty for
    # CSV-sourced rows (there's no equivalent raw payload).
    raw_payload = models.JSONField(blank=True, default=dict)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        unique_together = [("account", "external_id")]

    def __str__(self):
        return f"{self.date} {self.description[:40]} ({self.amount})"

    @property
    def is_credit(self):
        return self.amount > 0


class BankFeedSyncLog(models.Model):
    """One row per sync attempt (hourly cron, or a manual "Sync now" click)
    for one BankAccount -- what the Bank Feeds History table lists.
    Mirrors RecurringBillingRun's "always log it, even on failure" shape so
    a failed sync is visible, not silently swallowed."""

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    account = models.ForeignKey("BankAccount", on_delete=models.SET_NULL, null=True, related_name="sync_logs")
    status = models.CharField(max_length=10, choices=Status.choices)
    status_message = models.TextField(blank=True)
    transactions_fetched = models.PositiveIntegerField(default=0)
    transactions_new = models.PositiveIntegerField(default=0)
    transactions_matched = models.PositiveIntegerField(default=0)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Sync of {self.account} at {self.created_at} ({self.status})"
