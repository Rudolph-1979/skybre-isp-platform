from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import IntegrityError, models, transaction
from django.db.models import BigIntegerField, Max
from django.db.models.functions import Cast, Substr


class Tariff(models.Model):
    class ServiceType(models.TextChoices):
        INTERNET = "internet", "Internet"
        VOICE = "voice", "Voice"
        BUNDLE = "bundle", "Bundle"
        OTHER = "other", "Other"

    class BillingPeriod(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Quarterly"
        ANNUALLY = "annually", "Annually"

    name = models.CharField(max_length=150)
    service_type = models.CharField(max_length=20, choices=ServiceType.choices, default=ServiceType.INTERNET)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    billing_period = models.CharField(max_length=20, choices=BillingPeriod.choices, default=BillingPeriod.MONTHLY)
    # Kbps, matching network.ConnectionRule's speed_down_kbps/speed_up_kbps
    # so there is one speed unit across the platform. 4 Mbps is 4096.
    speed_download_kbps = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Download speed (Kbps)",
        help_text="In Kbps — 4 Mbps is 4096, 10 Mbps is 10240.",
    )
    speed_upload_kbps = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Upload speed (Kbps)",
        help_text="In Kbps — 4 Mbps is 4096, 10 Mbps is 10240.",
    )
    data_cap_gb = models.PositiveIntegerField(null=True, blank=True, help_text="Blank = unlimited")

    # --- Fair use -------------------------------------------------------
    # Deliberately SEPARATE from data_cap_gb above, and not a rename of it.
    # A cap is a bundle somebody bought and can run out of; fair use is a
    # threshold on an uncapped plan past which heavy users are shaped so
    # everyone else keeps working. They are different promises to the
    # customer, and the usage page shows data_cap_gb to the CUSTOMER -- so
    # enforcing fair use through that field would start showing people a
    # "cap" on a plan sold to them as uncapped.
    #
    # Blank = no fair-use policy, which is every existing plan. Nothing
    # changes for anybody until a number is put in here.
    fup_threshold_gb = models.PositiveIntegerField(
        null=True, blank=True,
        verbose_name="Fair-use threshold (GB)",
        help_text="Blank = no fair-use shaping. Usage past this in a calendar month shapes the line.",
    )
    fup_speed_pct = models.PositiveIntegerField(
        default=30,
        verbose_name="Shaped speed (% of plan)",
        help_text="What the line runs at once past the threshold. 30 = 30% of plan speed.",
    )
    tax_rate_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.price}/{self.billing_period})"


class IssuedNumberHighWater(models.Model):
    """The highest sequence number ever issued under each document prefix.

    Invoice._next_number_for_status takes one past the highest number on
    an EXISTING row, which is right until a row stops existing.
    InvoiceViewSet.destroy permits hard-deleting a real invoice, and
    deleting the newest one dropped the maximum back down -- so the next
    invoice created was issued a number a customer was already holding a
    PDF for. Two customers, one tax invoice number, which is the same
    failure 91e0b03 fixed from the other direction.

    A separate row per prefix rather than a column on Invoice, because the
    fact being recorded is about the sequence, not about any one document
    -- and it has to outlive every document in it.

    Consulted alongside the row maximum rather than instead of it, so the
    counter self-heals: an import that inserts numbers above it, or a
    counter that is somehow behind, still produces a number above
    everything that exists. Bumped only AFTER a successful save, so a
    create that fails does not burn a number and leave a gap in a
    sequence that has to explain itself to SARS.
    """

    prefix = models.CharField(max_length=10, unique=True)
    last_seq = models.PositiveBigIntegerField(default=0)

    class Meta:
        verbose_name = "issued number high-water mark"
        verbose_name_plural = "issued number high-water marks"

    def __str__(self):
        return f"{self.prefix}-{self.last_seq:06d}"


class Service(models.Model):
    """A customer's active subscription to a tariff/plan."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        TERMINATED = "terminated", "Terminated"
        PENDING = "pending", "Pending Activation"

    class ConnectionType(models.TextChoices):
        OVPN = "ovpn", "OVPN"
        PPPOE = "pppoe", "PPPoE"

    class IPAssignmentMode(models.TextChoices):
        MANUAL = "manual", "Manual (static public IP)"
        POOL = "pool", "Select from Customer IP Pool"
        AUTO = "auto", "Automatically assign from Customer IP Pool"

    customer = models.ForeignKey("customers.Customer", on_delete=models.CASCADE, related_name="services")
    tariff = models.ForeignKey(Tariff, on_delete=models.PROTECT, related_name="services")
    # --- a tariff change booked for a future date -------------------------
    # An upgrade or downgrade agreed today but taking effect later, which is
    # how it actually happens: the customer phones mid-month and the change
    # belongs at the start of their next billing period.
    #
    # Deliberately NOT applied by editing `tariff` early -- that would bill
    # and rate-limit them on the new plan from the moment you saved it. These
    # two fields hold the intent; billing.tariff_changes applies it on the day
    # and then clears them.
    pending_tariff = models.ForeignKey(
        Tariff, on_delete=models.SET_NULL, null=True, blank=True, related_name="pending_services",
        help_text="Tariff to switch this service to on the effective date below.",
    )
    pending_tariff_date = models.DateField(
        null=True, blank=True,
        verbose_name="Change takes effect",
        help_text=(
            "The date the new tariff starts applying. Set it to the first day of a billing period — "
            "a mid-period change is not prorated, so the whole period bills at the new price."
        ),
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    # Set when this service was suspended *because the customer* was set to
    # Suspended, rather than for a reason of its own (non-payment, a fault, a
    # staff decision about this one service).
    #
    # It exists so putting the customer back to Active restores exactly the
    # services that customer-level action took down, and leaves alone anything
    # suspended for its own reasons. Without it, reactivating a customer would
    # quietly un-suspend a service that the billing run had blocked for
    # non-payment -- handing back internet to someone who still hasn't paid.
    auto_suspended_with_customer = models.BooleanField(default=False, editable=False)
    device = models.ForeignKey(
        "network.Device", on_delete=models.SET_NULL, null=True, blank=True, related_name="services"
    )
    # WHERE THE CLIENT PHYSICALLY CONNECTS -- the tower AP or sector their
    # dish points at, the OLT their fibre lands on, the switch their cable
    # goes into.
    #
    # Deliberately separate from `device` above, which is the NAS: the
    # router PPPoE terminates on, and the one every RADIUS, shaper and
    # blocking action gets pushed to. On a wireless network those are
    # routinely different boxes -- fifty customers spread across six sector
    # radios can all terminate on one core router. Overloading `device` to
    # mean both would make "which customers does this AP serve"
    # unanswerable, and would break enforcement the first time somebody
    # pointed it at an AP that has no RADIUS relationship with the service.
    #
    # Nothing is enforced against this field. It documents the physical
    # path, and it earns its keep on the other side: the device page can
    # now answer "this AP is down, who is affected".
    access_device = models.ForeignKey(
        "network.Device", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="access_services",
        help_text="The AP, sector, OLT or switch this client actually connects to. Not the NAS — see `device`.",
    )
    # One field rather than three, because the answer is the same shape at
    # every layer and staff should not have to decide which box to use: a
    # sector on an AP, a PON port on an OLT, a port on a switch.
    access_detail = models.CharField(
        max_length=120, blank=True,
        help_text='Port, sector or SSID on that device — e.g. "Sector B 120°", "PON 1/3", "ether7".',
    )

    # --- Fair-use overrides for this one line ---------------------------
    # Null means "whatever the tariff says", so a plan's policy stays in
    # one place and an exception does not require inventing a new plan for
    # one customer. 0 in fup_threshold_gb is a real value meaning "shape
    # them immediately" -- which is why these are nullable rather than
    # zero-defaulted.
    fup_threshold_gb = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Overrides the tariff's fair-use threshold for this line only. Blank = use the tariff's.",
    )
    fup_speed_pct = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Overrides the tariff's shaped speed for this line only. Blank = use the tariff's.",
    )
    fup_exempt = models.BooleanField(
        default=False,
        help_text="Never shape this line, whatever the tariff says. For business lines and staff.",
    )
    # The last Mikrotik-Rate-Limit actually pushed to this line's live
    # session. Not a setting -- a record of what the router was last told,
    # so the scheduled policy run can push only what CHANGED. Without it a
    # five-minute cron would re-send an identical limit to every session
    # every run: hundreds of pointless packets an hour, and a router log
    # nobody can read.
    last_pushed_rate_limit = models.CharField(max_length=64, blank=True, editable=False)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # --- RADIUS / OVPN (Mikrotik @ Teraco JHB) -----------------------------
    # Staff set these manually per service (not auto-generated, not tied to
    # the customer's portal login). When set, radiusauth's sync signals
    # (see radiusauth/signals.py) mirror them into the standard FreeRADIUS
    # `radcheck`/`radreply` tables so the customer's OVPN client can
    # authenticate against this Service and automatically receive their
    # tariff's speed as a Mikrotik-Rate-Limit reply attribute.
    #
    # radius_password is intentionally stored in a form FreeRADIUS's SQL
    # `Cleartext-Password` check-item can use directly -- RADIUS PAP/CHAP
    # requires the server to hold a recoverable value (the wire protocol
    # itself encrypts credentials via the shared secret; FreeRADIUS decrypts
    # to plaintext to compare). This is unlike portal login passwords, which
    # remain one-way hashed and unrecoverable -- see the accounts app. The
    # API still exposes this write-only/masked, same pattern as elsewhere.
    radius_username = models.CharField(
        max_length=150, blank=True, null=True, unique=True,
        help_text="Login name the customer's Mikrotik/OVPN client authenticates with. Blank = RADIUS login disabled for this service.",
    )
    radius_password = models.CharField(max_length=128, blank=True, null=True)

    # --- PPPoE public IP assignment -----------------------------------
    # radius_connection_type distinguishes this service's RADIUS/NAS use
    # case: "ovpn" (the original Teraco Mikrotik OVPN setup -- its
    # Framed-IP-Address is always auto-allocated from a "network"-category
    # IP Pool via radiusauth.signals._allocate_network_ip, a path this
    # feature leaves completely unchanged) vs "pppoe" (a customer's PPPoE
    # router login, where staff choose how its public IP is handed out via
    # ip_assignment_mode below). Defaults to "ovpn" so every service that
    # existed before this field was added keeps behaving exactly as it did.
    radius_connection_type = models.CharField(
        max_length=10, choices=ConnectionType.choices, default=ConnectionType.OVPN,
        help_text="Which RADIUS/NAS setup this service's radius_username authenticates against.",
    )
    # Only meaningful when radius_connection_type='pppoe':
    #   manual -> static_ip below is sent verbatim as Framed-IP-Address.
    #   pool   -> staff pick one specific free address from ip_pool (see
    #             ServiceSerializer's write-only `ip_address` field).
    #   auto   -> the system auto-picks the next free address in ip_pool
    #             and keeps reusing it on every reconnect -- the same
    #             mechanism _allocate_network_ip already uses for OVPN,
    #             just scoped to a Customer-category pool instead of a Net
    #             one (see radiusauth/signals.py::_allocate_customer_ip).
    ip_assignment_mode = models.CharField(
        max_length=10, choices=IPAssignmentMode.choices, default=IPAssignmentMode.AUTO,
        help_text="How this PPPoE service's public IP is handed out. Ignored for OVPN services.",
    )
    static_ip = models.GenericIPAddressField(
        null=True, blank=True,
        help_text="Static public IP sent as Framed-IP-Address when ip_assignment_mode='manual'. Not tracked in any IP Pool.",
    )
    ip_pool = models.ForeignKey(
        "network.IPPool", on_delete=models.SET_NULL, null=True, blank=True, related_name="pppoe_services",
        help_text="Customer IP Pool this service's address comes from when ip_assignment_mode is 'pool' or 'auto'.",
    )

    # --- Live-API shaper override ---------------------------------------
    # Optional per-service speed override, used instead of the tariff's own
    # plan speed when this device's Shaper is on (see
    # network.router_sync.effective_speed_kbps). Must belong to the same
    # device this service is on -- enforced in ServiceSerializer.
    connection_rule = models.ForeignKey(
        "network.ConnectionRule", on_delete=models.SET_NULL, null=True, blank=True, related_name="services",
        help_text="Optional speed override for this specific service instead of its tariff's plan speed. Must belong to this service's device.",
    )

    def __str__(self):
        return f"{self.customer} -> {self.tariff}"

    @property
    def has_pending_tariff_change(self):
        return bool(self.pending_tariff_id and self.pending_tariff_date)

    @property
    def public_ip(self):
        """The public address this service is actually handing out, or None.

        One definition, because two screens show it now -- the service row on
        a customer's page and the Public IP column on the customer list -- and
        two copies of this rule would eventually disagree about the same
        customer.

        Deliberately loops over ip_addresses.all() in Python rather than
        .filter(...).first(): a filtered related manager issues its own query
        and ignores prefetch_related entirely, which on a 50-row customer list
        is 50 extra queries for one column.
        """
        from network.models import IPPool

        if self.radius_connection_type != Service.ConnectionType.PPPOE:
            # An OVPN service's address comes from the VPN server, not from
            # anything stored on the service, so static_ip here is stale.
            return None
        if self.ip_assignment_mode == Service.IPAssignmentMode.MANUAL:
            return self.static_ip or None
        for addr in self.ip_addresses.all():
            if addr.pool.category == IPPool.Category.CUSTOMER:
                return addr.address
        return None


class Invoice(models.Model):
    class Status(models.TextChoices):
        QUOTE = "quote", "Quote"
        PROFORMA = "proforma", "Pro Forma Invoice"
        DRAFT = "draft", "Draft"
        UNPAID = "unpaid", "Unpaid"
        PAID = "paid", "Paid"
        OVERDUE = "overdue", "Overdue"
        CANCELLED = "cancelled", "Cancelled"

    # Statuses that come *before* a document is a real (or soon-to-be-real)
    # tax invoice. A record starts life as a Quote, can be converted to a
    # Pro Forma, and from either of those can be converted to a real
    # Invoice -- one-directional, no going back. See convert_to_proforma()/
    # convert_to_invoice() below.
    PRE_INVOICE_STATUSES = (Status.QUOTE, Status.PROFORMA)

    # `number` prefix used at each stage of that lifecycle. Quote and Pro
    # Forma each get their own separate, clearly-labelled sequence so
    # neither one consumes a number from the real invoice sequence --
    # important since some of them will never convert. Every other status
    # (draft/unpaid/paid/overdue/cancelled) is a real invoice and they all
    # share one continuous "INV" sequence, same as before this feature.
    _NUMBER_PREFIXES = {
        Status.QUOTE: "QUO",
        Status.PROFORMA: "PF",
    }
    _DEFAULT_PREFIX = "INV"

    number = models.CharField(max_length=30, unique=True, editable=False)
    customer = models.ForeignKey("customers.Customer", on_delete=models.CASCADE, related_name="invoices")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    date_created = models.DateField(auto_now_add=True)
    date_due = models.DateField()
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    note = models.TextField(blank=True)
    # Set only when this document was generated by the recurring-billing
    # engine (see RecurringBillingRun below) rather than the "+ New quote"/
    # "+ New invoice" buttons -- lets a run's History row show exactly what
    # it created, purely for traceability. SET_NULL so deleting a run
    # record (there's no UI for that today, but just in case) never takes
    # the invoice down with it.
    created_by_run = models.ForeignKey(
        "RecurringBillingRun", on_delete=models.SET_NULL, null=True, blank=True, related_name="invoices_created"
    )
    # Whether this invoice's total is currently included in
    # Customer.balance. THREE states, not two:
    #
    #   True  -- this invoice's total is in the balance; releasing it
    #            takes the total back off.
    #   False -- it is not, and this invoice is managed by
    #            apply_balance_debit(), so issuing it will add it.
    #   NULL  -- unknown. Every row that predates this feature. The
    #            balance already reflects whatever the old code did to it
    #            (recurring._generate_document debited; nothing else did),
    #            and there is no way to tell after the fact which is which:
    #            a run-created invoice born UNPAID is indistinguishable
    #            from a run-created pro forma that was later converted.
    #
    # NULL is inert -- apply_balance_debit() and release_balance_debit()
    # both leave it completely alone. That matters because the first
    # version of this field defaulted every existing row to False, which
    # is not neutral: False means "not yet debited", so the first PATCH of
    # any legacy UNPAID invoice re-added a total that was already in the
    # balance and silently doubled the customer's debt. Reasoning only
    # about the reversal direction missed it.
    #
    # Historical rows therefore keep exactly the balance contribution they
    # have today, right or wrong. What that contribution actually is, is a
    # data question -- answered by `manage.py balance_drift`, and repaired
    # deliberately, never guessed at by a migration.
    balance_debited = models.BooleanField(default=False, null=True, editable=False)

    # Statuses whose total counts toward what the customer owes: issued,
    # and not cancelled. A draft is not issued yet; a quote/pro forma is
    # not an invoice; a cancelled invoice is not owed. PAID is included
    # because the debit stays and the payment's own credit offsets it --
    # removing it would double-count the payment.
    DEBITED_STATUSES = (Status.UNPAID, Status.PAID, Status.OVERDUE)

    class Meta:
        ordering = ["-date_created"]

    def _prefix_for_status(self, status):
        return self._NUMBER_PREFIXES.get(status, self._DEFAULT_PREFIX)

    def _next_number_for_status(self, status):
        """One past the HIGHEST sequence number ever issued under this
        status's prefix.

        Deliberately the maximum, not the number on the most recently
        created row. Those are the same thing right up until they aren't:
        an imported or backdated invoice carrying a lower number than the
        newest row would otherwise drag the sequence backwards and start
        handing out numbers that are already taken -- and because the
        clash is on a unique column, every subsequent create walks
        forward one number at a time hitting the same wall.

        Rows whose number doesn't parse as `<prefix>-<digits>` (legacy
        imports, hand-edited references) are skipped rather than guessed
        at: they're excluded by the regex before the cast, so a single
        unparseable row can't take the sequence down with it. The digit
        bound keeps the cast safe -- nine digits is ~1000x more invoices
        than this platform will ever issue, and anything longer is
        malformed by definition.
        """
        prefix = self._prefix_for_status(status)
        qs = Invoice.objects.filter(number__regex=rf"^{prefix}-\d{{1,9}}$")
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        highest = qs.annotate(
            _seq=Cast(Substr("number", len(prefix) + 2), BigIntegerField())
        ).aggregate(highest=Max("_seq"))["highest"]
        # ...and never below a number that has already been issued once,
        # even if the row carrying it has since been deleted. See
        # IssuedNumberHighWater: taking the maximum of existing rows alone
        # meant deleting the newest invoice handed its number straight to
        # the next one.
        watermark = (
            IssuedNumberHighWater.objects.filter(prefix=prefix)
            .values_list("last_seq", flat=True)
            .first()
        )
        return f"{prefix}-{max(highest or 0, watermark or 0) + 1:06d}"

    def _record_issued_number(self):
        """Remember that this number has been used, so it is never reused.

        After the save rather than before it, so a create that fails
        leaves no gap. Written with Greatest() so two concurrent creates
        cannot move the mark backwards.
        """
        from django.db.models import Value
        from django.db.models.functions import Greatest

        prefix, _, seq = (self.number or "").rpartition("-")
        if not prefix or not seq.isdigit():
            return
        updated = IssuedNumberHighWater.objects.filter(prefix=prefix).update(
            last_seq=Greatest("last_seq", Value(int(seq)))
        )
        if not updated:
            try:
                IssuedNumberHighWater.objects.create(prefix=prefix, last_seq=int(seq))
            except IntegrityError:
                # Another create got there first; its own Greatest() call
                # covers this number too.
                IssuedNumberHighWater.objects.filter(prefix=prefix).update(
                    last_seq=Greatest("last_seq", Value(int(seq)))
                )

    def save(self, *args, **kwargs):
        if self.number:
            return super().save(*args, **kwargs)

        # Two simultaneous creates can compute the same next number --
        # `number` is unique, so the constraint is the only thing that
        # actually serialises them, and the loser gets an IntegrityError.
        # Retry rather than surfacing a 500; inside a recurring-billing
        # run, an unhandled clash here used to be enough to take that
        # customer's whole invoice down with it.
        #
        # Mirrors customers.Customer.save()'s handling of exactly this
        # race on customer_id, down to the attempt count. Each attempt
        # gets its own atomic block so the failed INSERT can be rolled
        # back and retried; anything that isn't a number clash is
        # re-raised untouched. Retries converge because
        # _next_number_for_status re-reads the maximum each time, so the
        # winner's number is visible to the next attempt.
        last_error = None
        for _ in range(5):
            self.number = self._next_number_for_status(self.status)
            try:
                with transaction.atomic():
                    result = super().save(*args, **kwargs)
                    self._record_issued_number()
                    return result
            except IntegrityError as exc:
                if "number" not in str(exc):
                    raise
                last_error = exc
                self.number = ""
        raise last_error

    @property
    def balance_due(self):
        return self.total - self.paid_amount

    def apply_balance_debit(self):
        """Bring Customer.balance in line with whether this invoice is owed.

        Idempotent, and driven by the gap between `balance_debited` (what
        the balance currently reflects) and DEBITED_STATUSES (what it
        should reflect). Safe to call after any save, and safe to call
        twice.

        Before this, only the recurring-billing engine ever debited a
        balance -- recurring._generate_document did it inline, with a
        comment noting that "manually-created invoices are
        unaffected/unchanged". But PaymentSerializer.create credited the
        balance for ANY payment. So the two halves of the ledger disagreed
        for every invoice not raised by the engine: staff raise a R1,000
        invoice by hand (balance unchanged), the customer pays it (balance
        -R1,000), and the customer's portal, their statement PDF and every
        email now show R1,000 of credit that does not exist -- while
        blocking_candidate_services' `balance <= minimum_balance` test
        exempts them from suspension permanently.

        Every path that can change whether an invoice is owed calls this:
        creation, quote/pro forma conversion, a status edit, and deletion
        (which calls it after moving the status to cancelled, or via
        release_balance_debit below).

        F() rather than read-modify-write, for the same reason
        Payment.reverse_ledger_effect uses it: the balance is the field
        two concurrent finance actions race on.
        """
        from django.db.models import F

        # NULL means this invoice predates the flag and the balance already
        # reflects whatever the old code did. Touching it would either
        # double a debit that is already there or credit one that never
        # was, so it is left alone entirely. See the field's comment.
        if self.balance_debited is None:
            return

        should_be_debited = self.status in self.DEBITED_STATUSES
        if should_be_debited == self.balance_debited:
            return

        delta = self.total if should_be_debited else -self.total
        type(self.customer).objects.filter(pk=self.customer_id).update(
            balance=F("balance") + delta
        )
        self.balance_debited = should_be_debited
        # update() rather than save(), so this can never recurse through a
        # save-triggered signal and can never write a stale copy of any
        # other field back over a concurrent edit.
        Invoice.objects.filter(pk=self.pk).update(balance_debited=self.balance_debited)

    def release_balance_debit(self):
        """Drop this invoice's debit off the customer's balance, for a
        delete rather than a status change.

        Deleting an invoice used to leave its debit behind forever: the
        recurring engine had already added the total to the balance, and
        InvoiceViewSet.destroy removed the only record of why. The
        customer was then chased for -- and eventually suspended over --
        money that no invoice claimed.
        """
        from django.db.models import F

        if not self.balance_debited:
            return
        type(self.customer).objects.filter(pk=self.customer_id).update(
            balance=F("balance") - self.total
        )
        self.balance_debited = False
        Invoice.objects.filter(pk=self.pk).update(balance_debited=False)

    def can_convert_to_proforma(self):
        return self.status == self.Status.QUOTE

    def can_convert_to_invoice(self):
        return self.status in self.PRE_INVOICE_STATUSES

    def convert_to_proforma(self):
        """Quote -> Pro Forma only. One-directional: a Pro Forma or real
        Invoice can never be converted back into a Quote."""
        if not self.can_convert_to_proforma():
            raise ValueError("Only a quote can be converted to a pro forma invoice.")
        self.status = self.Status.PROFORMA
        # Cleared rather than assigned here so save() is the single place
        # that issues a number -- and so this path gets its retry-on-clash
        # protection too, instead of 500ing on a race the create path
        # already survives.
        self.number = ""
        self.save()

    def convert_to_invoice(self):
        """Quote -> Invoice or Pro Forma -> Invoice. Assigns a real,
        gapless invoice number the moment this stops being a quote/pro
        forma and becomes an actual tax invoice, and activates any
        tariff-plan line items into real Service subscriptions."""
        if not self.can_convert_to_invoice():
            raise ValueError("Only a quote or a pro forma invoice can be converted to an invoice.")
        self.status = self.Status.UNPAID
        # See convert_to_proforma: save() issues the number, with retries.
        self.number = ""
        self.save()
        # The moment it stops being a quote/pro forma it is money owed.
        self.apply_balance_debit()
        self.activate_tariff_services()

    def recalc_totals(self):
        items = self.items.all()
        self.subtotal = sum((i.quantity * i.unit_price for i in items), start=0)
        self.tax_total = sum((i.quantity * i.unit_price * (i.tax_rate_pct / 100) for i in items), start=0)
        self.total = self.subtotal + self.tax_total
        # A quote/pro forma has no payments against it -- never auto-flip
        # those to "paid" just because totals happen to net to zero/match.
        if (
            self.status not in self.PRE_INVOICE_STATUSES
            and self.paid_amount >= self.total
            and self.total > 0
        ):
            self.status = self.Status.PAID
        self.save()

    def activate_tariff_services(self):
        """Once this document is a real invoice (created directly as one,
        or just converted from a Quote/Pro Forma), any tariff-plan line
        item that quoted a from/till period gets turned into a real
        Service subscription for this customer -- that's the point where
        the customer has actually committed, not just been quoted.
        Idempotent: skips any item that already has a service attached."""
        for item in self.items.filter(item_type=InvoiceItem.ItemType.TARIFF, tariff__isnull=False, service__isnull=True):
            service = Service.objects.create(
                customer=self.customer,
                tariff=item.tariff,
                status=Service.Status.ACTIVE,
                start_date=item.period_start,
                end_date=item.period_end,
            )
            item.service = service
            item.save(update_fields=["service"])

    def __str__(self):
        return self.number


class InvoiceItem(models.Model):
    class ItemType(models.TextChoices):
        CUSTOM = "custom", "Custom"
        PRODUCT = "product", "Stock item"
        TARIFF = "tariff", "Tariff plan"

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="items")
    service = models.ForeignKey(Service, on_delete=models.SET_NULL, null=True, blank=True)
    item_type = models.CharField(max_length=10, choices=ItemType.choices, default=ItemType.CUSTOM)
    # Set for item_type=PRODUCT -- a stock item being quoted/invoiced.
    product = models.ForeignKey(
        "inventory.Product", on_delete=models.SET_NULL, null=True, blank=True, related_name="invoice_items"
    )
    # Set for item_type=TARIFF -- a plan being quoted/invoiced.
    tariff = models.ForeignKey(
        Tariff, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoice_items"
    )
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    tax_rate_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    # Only meaningful for item_type=TARIFF -- the contract/service period
    # being quoted (e.g. a 12-month term). See Invoice.activate_tariff_services().
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)

    @property
    def total(self):
        return self.quantity * self.unit_price * (1 + self.tax_rate_pct / 100)

    def __str__(self):
        return f"{self.description} x{self.quantity}"


class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        CARD = "card", "Card"
        BANK_TRANSFER = "bank_transfer", "Bank Transfer"
        MOBILE_MONEY = "mobile_money", "Mobile Money"
        MANUAL = "manual", "Manual Adjustment"

    customer = models.ForeignKey("customers.Customer", on_delete=models.CASCADE, related_name="payments")
    invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.CASH)
    date = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=255, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="payments_received"
    )

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"Payment {self.amount} - {self.customer}"

    def reverse_ledger_effect(self):
        """Undo what recording this payment did to the ledger.

        There was no reversal path anywhere in billing before this. All the
        arithmetic lived in PaymentSerializer.create, so deleting a payment
        -- the obvious way to correct one applied to the wrong invoice --
        left the customer's balance credited and the invoice still marked
        Paid, and the money simply vanished from what the customer owed.

        The bank feed made that worse rather than better: deleting a
        payment deliberately puts its bank transaction back in the review
        queue (bankfeeds.signals._revert_to_review_queue) so it can be
        confirmed again against the right invoice -- which, with nothing
        reversing the first one, credited a single EFT to the customer
        twice.

        Uses F() and a locked invoice row rather than read-modify-write,
        because the balance is exactly the field two concurrent finance
        actions race on. Caller wraps this and the delete in one atomic
        block. Idempotent it is NOT -- call it once, immediately before
        deleting the row.
        """
        from django.db.models import F

        type(self.customer).objects.filter(pk=self.customer_id).update(
            balance=F("balance") + self.amount
        )
        if self.invoice_id is None:
            return
        invoice = Invoice.objects.select_for_update().get(pk=self.invoice_id)
        invoice.paid_amount = invoice.paid_amount - self.amount
        # A payment that had flipped the invoice to Paid no longer covers
        # it. The status it held before that flip isn't recoverable, so it
        # goes back to Unpaid -- the state an invoice with money still
        # outstanding is in. Anything that wasn't Paid is left alone: a
        # cancelled or draft invoice doesn't become unpaid because a
        # payment against it was reversed.
        if invoice.status == Invoice.Status.PAID and invoice.paid_amount < invoice.total:
            invoice.status = Invoice.Status.UNPAID
        invoice.save(update_fields=["paid_amount", "status"])


class CreditRequest(models.Model):
    """A request to credit a customer's account for a stated reason --
    billing error, goodwill gesture, service outage compensation, etc.
    Submitted by Accounts (or an admin standing in), and must be approved
    by Management (or an admin) before it takes effect. Approving reduces
    the customer's balance directly by the requested amount; rejecting
    leaves it untouched."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    customer = models.ForeignKey("customers.Customer", on_delete=models.CASCADE, related_name="credit_requests")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decision_note = models.CharField(max_length=255, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Credit {self.amount} — {self.customer} ({self.status})"


class InvoiceDeletionRequest(models.Model):
    """Deleting a Quote or Pro Forma Invoice isn't a plain staff action --
    same trust tier as deleting a Customer (see
    customers.CustomerDeletionRequest, whose request/decide shape this
    mirrors). Any staff member with Finance access can submit one; only
    Management (or Admin) can approve or reject it -- approving actually
    deletes the Invoice (and its line items, via cascade).

    Deliberately scoped to quote/pro-forma-status invoices only -- see
    InvoiceViewSet.destroy (which blocks direct DELETE for exactly those
    two statuses, same as CustomerViewSet.destroy does for Customer) and
    InvoiceDeletionRequestSerializer.validate_invoice. Real invoices
    (draft/unpaid/paid/overdue/cancelled) are untouched by this feature --
    they can still be deleted directly by staff with Finance access, same
    as before this was added."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    # SET_NULL, not CASCADE: once approved, the invoice itself is deleted,
    # but this request row survives as the audit trail of what happened and
    # why -- see invoice_display_number/customer below, snapshotted before
    # that happens since `invoice` will no longer be there to read them from.
    invoice = models.ForeignKey(
        Invoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="deletion_requests"
    )
    invoice_display_number = models.CharField(max_length=30, editable=False, blank=True)
    invoice_display_customer = models.CharField(max_length=255, editable=False, blank=True)
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decision_note = models.CharField(max_length=255, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if self.invoice and not self.invoice_display_number:
            self.invoice_display_number = self.invoice.number
            self.invoice_display_customer = self.invoice.customer.full_name
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Delete {self.invoice_display_number or 'invoice'} ({self.status})"


class PaymentMethod(models.Model):
    """A named payment convention (e.g. "EFT 1st", "Netcash Debit Order
    15th", "Cash") staff can tag onto a customer's billing config purely
    for reporting/reference -- like RadiusNasClient.realm, this doesn't
    drive any automated behavior on its own; the actual due-date/blocking
    timing comes from CustomerBillingConfig's own fields."""

    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class RecurringBillingFieldsMixin(models.Model):
    """Fields shared between BillingDefaults (the org-wide template) and
    CustomerBillingConfig (one customer's actual settings) -- kept in one
    place so the two can never drift out of sync on what a "reset to
    default" / "apply to existing customers" actually copies. See
    SHARED_FIELDS below for exactly what that is."""

    class PaymentPeriod(models.TextChoices):
        MONTHLY = "monthly", "1 month"
        QUARTERLY = "quarterly", "3 months"
        BIANNUALLY = "biannually", "6 months"
        ANNUALLY = "annually", "12 months"

    class ProformaTarget(models.TextChoices):
        CURRENT_MONTH = "current_month", "Current month"
        NEXT_MONTH = "next_month", "Next month"

    payment_period = models.CharField(max_length=20, choices=PaymentPeriod.choices, default=PaymentPeriod.MONTHLY)
    payment_method = models.ForeignKey(
        PaymentMethod, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # Capped at 1-28 (not 1-31) so a "billing day" is always a real day in
    # every month -- sidesteps the whole short-month/clamping question
    # rather than guessing what staff would want for day 29-31.
    billing_day = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(28)],
        help_text="Day of the month recurring invoices are generated (1-28).",
    )
    use_date_of_customer_creation = models.BooleanField(
        default=False, help_text="Bill on the anniversary of the customer's creation date instead of billing_day."
    )
    payment_due_days = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Days after the document date payment is due. Blank = due the same day it's issued."
    )
    blocking_period_days = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Days after payment is due before this customer's services are automatically suspended. Blank = never auto-suspend.",
    )
    deactivation_period_days = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Days after blocking before further action is taken. Configurable, but not yet wired to any action -- reserved for a future release.",
    )
    minimum_balance = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="How far into credit/negative a customer's balance can go before the blocking period above starts counting.",
    )
    auto_create_invoices = models.BooleanField(default=True)
    send_billing_notifications = models.BooleanField(default=True)
    auto_proforma_enabled = models.BooleanField(
        default=False, help_text="Generate a pro forma invoice instead of a real invoice each cycle."
    )
    proforma_day = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(28)]
    )
    proforma_payment_period = models.CharField(max_length=20, choices=PaymentPeriod.choices, blank=True)
    create_proforma_for = models.CharField(
        max_length=20, choices=ProformaTarget.choices, default=ProformaTarget.CURRENT_MONTH
    )
    # Per-customer day offsets -- only actually fire if the matching
    # reminder is ALSO enabled globally, see ReminderSettings below.
    reminder_enabled = models.BooleanField(default=False)
    reminder_1_day = models.PositiveSmallIntegerField(null=True, blank=True)
    reminder_2_day = models.PositiveSmallIntegerField(null=True, blank=True)
    reminder_3_day = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        abstract = True

    # Every field a "reset to default" / "apply to existing customers"
    # bulk-copy touches -- deliberately excludes billing_enabled (opt-in,
    # see CustomerBillingConfig's docstring) and anything model-specific
    # (customer FK, billing_name/street/zip/city, next_billing_date,
    # timestamps).
    SHARED_FIELDS = [
        "payment_period", "payment_method_id", "billing_day", "use_date_of_customer_creation",
        "payment_due_days", "blocking_period_days", "deactivation_period_days", "minimum_balance",
        "auto_create_invoices", "send_billing_notifications", "auto_proforma_enabled", "proforma_day",
        "proforma_payment_period", "create_proforma_for", "reminder_enabled",
        "reminder_1_day", "reminder_2_day", "reminder_3_day",
    ]


class BillingDefaults(RecurringBillingFieldsMixin):
    """Singleton (pk=1) org-wide template for recurring billing -- seeds a
    new CustomerBillingConfig the first time one is created for a customer
    (see CustomerBillingConfig.for_customer()) and is what every field's
    "Reset to default" pulls from on an individual customer's own config.

    Deliberately has NO billing_enabled field: whether billing actually
    runs for a given customer is always a specific, deliberate decision
    made on that customer's own config, never something a global default
    (or "Apply to existing customers") can switch on in bulk."""

    # Skybre's own VAT registration number -- deliberately lives here
    # (not on RecurringBillingFieldsMixin, so it never gets copied onto a
    # per-customer CustomerBillingConfig) since it's a single company-wide
    # fact, not something that varies per customer. Shown on the
    # Accountant -> VAT Returns report so the SARS submission has it on
    # hand; blank until set from Configs -> Billing -> Billing Defaults.
    vat_number = models.CharField(max_length=32, blank=True, help_text="Skybre's own SARS VAT registration number.")

    # --- Company identity, as printed on tax invoices --------------------
    # A tax invoice has to carry the supplier's registered name, address and
    # VAT number to be valid, and none of that existed anywhere in the
    # platform before this -- the PDF was printing a bare brand name.
    company_legal_name = models.CharField(
        max_length=200, blank=True,
        help_text='Registered name as it must appear on a tax invoice, e.g. "Skybre Pty Ltd".',
    )
    company_address = models.CharField(
        max_length=255, blank=True, help_text="Street address line, e.g. Cnr Reitz & Botha Street.",
    )
    company_city = models.CharField(max_length=100, blank=True)
    company_postal_code = models.CharField(max_length=20, blank=True)
    company_country = models.CharField(max_length=100, blank=True, default="South Africa")
    company_phone = models.CharField(max_length=40, blank=True)
    company_email = models.EmailField(
        blank=True, help_text="Billing address shown on invoices, e.g. accounts@skybre.co.za.",
    )
    logo = models.ImageField(
        upload_to="branding/", null=True, blank=True,
        help_text="Printed top-left on invoices and statements. A PNG with a transparent background works best.",
    )

    # --- Banking, for the payment-details block on an invoice ------------
    # Deliberately separate from bankfeeds.BankAccount: that model is about
    # importing transactions from a bank API, may hold several accounts, and
    # none of them is necessarily the one customers should pay into. What
    # gets printed on an invoice is a single deliberate choice.
    bank_name = models.CharField(max_length=100, blank=True, help_text="e.g. FNB.")
    bank_account_number = models.CharField(max_length=40, blank=True)
    bank_branch_code = models.CharField(max_length=20, blank=True, help_text="e.g. 250655.")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Billing defaults"
        verbose_name_plural = "Billing defaults"

    def __str__(self):
        return "Billing defaults"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ReminderSettings(models.Model):
    """Singleton (pk=1) global kill-switches for payment reminders. A
    customer's own reminder_1_day/2/3 (see CustomerBillingConfig /
    RecurringBillingFieldsMixin) only take effect if the matching switch
    here is also on -- individual customer settings can't override this,
    same convention the Splynx reference uses."""

    static_days = models.BooleanField(
        default=True,
        help_text="Reminders fire this many days before/after due date (a fixed number per reminder). Dynamic day calculation isn't implemented yet.",
    )
    reminder_1_enabled = models.BooleanField(default=True)
    reminder_2_enabled = models.BooleanField(default=True)
    reminder_3_enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Reminder settings"
        verbose_name_plural = "Reminder settings"

    def __str__(self):
        return "Reminder settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class SuspensionSettings(models.Model):
    """Singleton (pk=1) global master switch for auto-suspension --
    mirrors ReminderSettings' "global kill-switch on top of per-customer
    settings" shape. A customer's own blocking_period_days (see
    RecurringBillingFieldsMixin) only actually suspends anything if this
    switch is ALSO on; individual customers can't override this, same
    convention as ReminderSettings. Deliberately its own tiny model rather
    than a field on BillingDefaults -- BillingDefaults is a per-customer
    *template* (each field gets copied onto, and can then be overridden
    per, a CustomerBillingConfig), whereas this is a true platform-wide
    switch nothing per-customer can bypass, the same role ReminderSettings
    already plays for reminders.

    Defaults to OFF on a fresh install/migrate, matching the behavior this
    replaces (auto-suspension was previously only reachable by editing
    every customer's blocking_period_days by hand, with no single place to
    confirm or flip it off in a hurry)."""

    auto_suspend_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Master switch for auto-suspension. When off, the recurring-billing engine never "
            "suspends anyone, regardless of any customer's own blocking_period_days. When on, "
            "suspension still only applies to customers who are billing_enabled AND have a "
            "blocking_period_days set."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Suspension settings"
        verbose_name_plural = "Suspension settings"

    def __str__(self):
        return "Suspension settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class CustomerBillingConfig(RecurringBillingFieldsMixin):
    """Per-customer recurring-billing configuration -- see
    RecurringBillingFieldsMixin for the shared fields this mirrors from
    BillingDefaults. Deliberately opt-in: billing_enabled defaults to False
    for every customer (including ones touched by
    BillingDefaults.apply_to_existing_customers()), so turning this
    feature on for a given customer is always a specific action a staff
    member takes, never a side effect of a global settings change."""

    customer = models.OneToOneField("customers.Customer", on_delete=models.CASCADE, related_name="billing_config")
    billing_enabled = models.BooleanField(
        default=False,
        help_text="Whether recurring invoicing/reminders/auto-suspension run for this customer at all.",
    )
    # Optional override for finance documents -- customer's own
    # address/city/etc. is used if these are left blank.
    billing_name = models.CharField(max_length=255, blank=True)
    billing_street = models.CharField(max_length=255, blank=True)
    billing_zip = models.CharField(max_length=20, blank=True)
    billing_city = models.CharField(max_length=100, blank=True)
    # Tracks the next date this customer is due to be (re-)invoiced -- set
    # the first time the recurring-billing engine bills them, then advanced
    # by payment_period each time, so re-running the engine on the same (or
    # a later, still-within-cycle) day never double-bills them.
    next_billing_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Billing config for {self.customer}"

    @classmethod
    def for_customer(cls, customer):
        """Get-or-create, seeded from BillingDefaults the first time --
        same lazy-singleton convention as EmailSettings.load(), just keyed
        per customer rather than platform-wide."""
        obj, created = cls.objects.get_or_create(customer=customer)
        if created:
            obj.apply_defaults(BillingDefaults.load())
        return obj

    def apply_defaults(self, defaults):
        """Copies every SHARED_FIELDS value from BillingDefaults onto this
        customer's config -- deliberately never touches billing_enabled.
        Used both the first time a config is created and by "Apply to
        existing customers" (see BillingDefaultsView.apply_to_existing)."""
        for field in self.SHARED_FIELDS:
            setattr(self, field, getattr(defaults, field))
        self.save()


class RecurringBillingRun(models.Model):
    """One row per committed run of the recurring-billing engine (see
    billing.recurring.run_recurring_billing) -- what Finance -> Recurring
    Billing's History table lists. A *preview* (commit=False) never
    creates one of these; only an actual Run does, whether it succeeds or
    fails partway (see status/status_message)."""

    class Status(models.TextChoices):
        PROCESSED = "processed", "Processed"
        FAILED = "failed", "Failed"

    run_date = models.DateField(help_text="The billing date this run was for.")
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PROCESSED)
    status_message = models.CharField(max_length=255, blank=True)
    partners = models.ManyToManyField(
        "customers.Partner", blank=True, related_name="recurring_billing_runs",
        help_text="Which partners' customers this run was scoped to. Empty = every partner.",
    )
    invoices_created_count = models.PositiveIntegerField(default=0)
    proforma_invoices_created_count = models.PositiveIntegerField(default=0)
    reminders_sent_count = models.PositiveIntegerField(default=0)
    suspensions_applied_count = models.PositiveIntegerField(default=0)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="The staff member who clicked Run. Every run is staff-triggered this release -- see the design doc's phased rollout.",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Recurring billing run {self.run_date} ({self.status})"
