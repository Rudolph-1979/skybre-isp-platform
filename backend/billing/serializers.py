from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from config.media_security import sign_media_path
from network.models import IPPool
from radiusauth.signals import assign_specific_customer_ip, release_customer_ip, sync_service_radius
from .models import (
    Tariff, Service, Invoice, InvoiceItem, Payment, CreditRequest, InvoiceDeletionRequest,
    PaymentMethod, BillingDefaults, ReminderSettings, SuspensionSettings, CustomerBillingConfig,
    RecurringBillingRun,
)


class TariffSerializer(serializers.ModelSerializer):
    # How many services are on this plan, and how many of those are live.
    # Editing a tariff is not a private act: the price decides what every
    # service on it is billed at the next run, and the speed is pushed to the
    # router as a rate limit for each one (see
    # radiusauth.signals._tariff_post_save_resync_services). The edit form
    # shows these counts so nobody changes a number without knowing the reach.
    #
    # Annotated on the queryset -- see TariffViewSet.get_queryset -- so listing
    # tariffs stays one query rather than two per row.
    # Read from the annotation where there is one, counted directly otherwise.
    # The fallback is for the single-object create/update responses, which
    # serialize the saved instance rather than a row off the annotated
    # queryset -- without it those responses carry null and the edit form's
    # warning has nothing to show.
    service_count = serializers.SerializerMethodField()
    active_service_count = serializers.SerializerMethodField()

    class Meta:
        model = Tariff
        fields = "__all__"
        read_only_fields = ["id", "created_at"]

    def get_service_count(self, obj):
        annotated = getattr(obj, "service_count", None)
        return annotated if annotated is not None else obj.services.count()

    def get_active_service_count(self, obj):
        annotated = getattr(obj, "active_service_count", None)
        if annotated is not None:
            return annotated
        return obj.services.filter(status=Service.Status.ACTIVE).count()


class ServiceSerializer(serializers.ModelSerializer):
    tariff_name = serializers.CharField(source="tariff.name", read_only=True)
    pending_tariff_name = serializers.CharField(source="pending_tariff.name", read_only=True, default=None)
    pending_tariff_price = serializers.DecimalField(
        source="pending_tariff.price", max_digits=10, decimal_places=2, read_only=True, default=None
    )
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    price = serializers.DecimalField(source="tariff.price", max_digits=10, decimal_places=2, read_only=True)
    # Never echo the RADIUS secret back -- write_only accepts it on
    # create/update, and `radius_password_set` tells the frontend whether one
    # is already on file, same write-only-secret pattern used for staff
    # accounts and SMTP settings elsewhere in this project.
    radius_password = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True)
    radius_password_set = serializers.SerializerMethodField()
    ip_pool_name = serializers.CharField(source="ip_pool.name", read_only=True, default=None)
    # The IP this service is actually handing out right now, if any --
    # static_ip verbatim for manual mode, or whichever Customer IP Pool
    # address is currently assigned_service=this for pool/auto mode. Purely
    # informational (read-only); the real allocation happens in
    # radiusauth/signals.py.
    assigned_ip = serializers.SerializerMethodField()
    # The outcome of the last attempt to push a change to this line --
    # see get_last_radius_action for why it is on screen at all.
    last_radius_action = serializers.SerializerMethodField()
    # Write-only: a specific network.IPAddress id to assign when
    # ip_assignment_mode='pool'. Ignored for manual/auto/ovpn. Omit/null to
    # leave whatever's currently assigned untouched.
    ip_address = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    connection_rule_name = serializers.CharField(source="connection_rule.title", read_only=True, default=None)
    # Where the client physically connects. Read-side extras so a service
    # row can be rendered without a second request per row, and so the
    # SITE comes along -- "Tower 3" is what somebody says on the phone,
    # not the name of the radio bolted to it.
    access_device_name = serializers.CharField(source="access_device.name", read_only=True, default=None)
    access_device_type = serializers.CharField(
        source="access_device.get_device_type_display", read_only=True, default=None
    )
    access_site_name = serializers.CharField(source="access_device.site.title", read_only=True, default=None)

    class Meta:
        model = Service
        fields = [
            "id", "customer", "customer_name", "tariff", "tariff_name", "price",
            "pending_tariff", "pending_tariff_name", "pending_tariff_price", "pending_tariff_date",
            "status", "device", "start_date", "end_date", "created_at",
            "access_device", "access_device_name", "access_device_type",
            "access_site_name", "access_detail",
            "fup_threshold_gb", "fup_speed_pct", "fup_exempt",
            "radius_username", "radius_password", "radius_password_set",
            "radius_connection_type", "ip_assignment_mode", "static_ip",
            "ip_pool", "ip_pool_name", "ip_address", "assigned_ip",
            "connection_rule", "connection_rule_name", "last_radius_action",
        ]
        read_only_fields = ["id", "created_at"]

    def get_radius_password_set(self, obj):
        return bool(obj.radius_password)

    def get_assigned_ip(self, obj):
        # Moved onto the model as Service.public_ip so the customer list's
        # Public IP column answers this the same way this row does.
        return obj.public_ip

    def get_last_radius_action(self, obj):
        """What happened the last time a change was pushed to this line.

        On screen because it used to be nowhere. Enforcement runs after the
        response has gone out, so a failure to reach the router was invisible:
        the screen said Saved, the customer's speed didn't change, and nothing
        connected the two. A staff member should be able to see, on the
        service itself, that the last change never landed.
        """
        actions = getattr(obj, "recent_radius_actions", None)
        if actions is None:
            actions = list(obj.radius_actions.order_by("-created_at")[:1])
        if not actions:
            return None
        action = actions[0]
        return {
            "ok": action.ok,
            "action": action.action,
            "transport": action.transport,
            "detail": action.detail,
            "at": action.created_at,
        }

    def _field(self, attrs, name, default=None):
        """Value for `name` as it will be *after* this save -- from attrs
        if being changed, else whatever the instance already has (or
        `default` on create, when there's no instance yet)."""
        if name in attrs:
            return attrs[name]
        if self.instance is not None:
            return getattr(self.instance, name)
        return default

    def validate(self, attrs):
        conn_type = self._field(attrs, "radius_connection_type", Service.ConnectionType.OVPN)
        mode = self._field(attrs, "ip_assignment_mode", Service.IPAssignmentMode.AUTO)

        # --- a booked tariff change needs both halves, and a future date ---
        # Half of one is the dangerous shape: a tariff with no date would sit
        # there forever and never apply, and a date with no tariff would look
        # like something was scheduled when nothing was.
        pending_tariff = self._field(attrs, "pending_tariff")
        pending_date = self._field(attrs, "pending_tariff_date")
        if pending_tariff and not pending_date:
            raise serializers.ValidationError(
                {"pending_tariff_date": "Give the date the new tariff should take effect."}
            )
        if pending_date and not pending_tariff:
            raise serializers.ValidationError(
                {"pending_tariff": "Choose the tariff to change to on that date."}
            )
        if pending_tariff and pending_date:
            current_tariff = self._field(attrs, "tariff")
            if current_tariff and pending_tariff.pk == current_tariff.pk:
                raise serializers.ValidationError(
                    {"pending_tariff": "That's already this service's tariff — nothing to change."}
                )
            # A past date would be applied on the next run, which is a
            # surprise rather than a schedule. Today is allowed: "change it
            # now, and bill this period on the new plan" is a real request.
            if pending_date < timezone.localdate():
                raise serializers.ValidationError(
                    {"pending_tariff_date": "That date has passed. Pick today or a future date."}
                )

        if conn_type == Service.ConnectionType.PPPOE:
            if mode == Service.IPAssignmentMode.MANUAL:
                if not self._field(attrs, "static_ip"):
                    raise serializers.ValidationError(
                        {"static_ip": "Enter a static public IP for manual IP assignment."}
                    )
            elif mode in (Service.IPAssignmentMode.POOL, Service.IPAssignmentMode.AUTO):
                pool = self._field(attrs, "ip_pool")
                if not pool:
                    raise serializers.ValidationError({"ip_pool": "Select a Customer IP Pool."})
                if pool.category != IPPool.Category.CUSTOMER:
                    raise serializers.ValidationError(
                        {"ip_pool": "Only a Customer IP Pool can be used for PPPoE IP assignment."}
                    )
                if mode == Service.IPAssignmentMode.POOL:
                    has_existing = (
                        self.instance is not None
                        and self.instance.ip_addresses.filter(pool__category=IPPool.Category.CUSTOMER).exists()
                    )
                    if "ip_address" not in attrs and not has_existing:
                        raise serializers.ValidationError(
                            {"ip_address": "Select a specific IP address from the pool."}
                        )

        connection_rule = self._field(attrs, "connection_rule")
        if connection_rule is not None:
            device = self._field(attrs, "device")
            if device is not None and connection_rule.device_id != device.id:
                raise serializers.ValidationError(
                    {"connection_rule": "This Connection Rule belongs to a different router than this service's device."}
                )
        return attrs

    def _apply_ip_address_selection(self, instance, address_id):
        if address_id is None:
            return
        try:
            assign_specific_customer_ip(instance, address_id)
        except ValueError as exc:
            raise serializers.ValidationError({"ip_address": str(exc)})
        # The address is now attached -- regenerate radreply so
        # Framed-IP-Address reflects it immediately rather than waiting
        # for this service's next unrelated save.
        sync_service_radius(instance)

    def update(self, instance, validated_data):
        # A blank/omitted radius_password means "leave it as-is" -- only a
        # non-empty value actually overwrites the stored secret. Sending an
        # explicit blank string would otherwise clear it, which the "is set"
        # checkbox pattern relies on the frontend never doing accidentally.
        if "radius_password" in validated_data and not validated_data["radius_password"]:
            validated_data.pop("radius_password")
        address_id = validated_data.pop("ip_address", None)

        # If the Customer IP Pool selection itself is changing, the address
        # held under the OLD pool is stale -- free it now so this doesn't
        # leave two pools' worth of addresses attached to one service.
        new_pool = validated_data.get("ip_pool", instance.ip_pool)
        if instance.pk and instance.ip_pool_id and new_pool != instance.ip_pool:
            release_customer_ip(instance)

        instance = super().update(instance, validated_data)
        self._apply_ip_address_selection(instance, address_id)
        return instance

    def create(self, validated_data):
        if not validated_data.get("radius_password"):
            validated_data.pop("radius_password", None)
        address_id = validated_data.pop("ip_address", None)
        instance = super().create(validated_data)
        self._apply_ip_address_selection(instance, address_id)
        return instance


class InvoiceItemSerializer(serializers.ModelSerializer):
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True, default=None)
    tariff_name = serializers.CharField(source="tariff.name", read_only=True, default=None)

    class Meta:
        model = InvoiceItem
        fields = [
            "id", "invoice", "service", "item_type", "product", "product_name", "tariff", "tariff_name",
            "description", "quantity", "unit_price", "tax_rate_pct", "period_start", "period_end", "total",
        ]
        # `invoice` and `service` are read-only: this serializer is only
        # ever used nested (inside InvoiceSerializer for reads, inside
        # InvoiceCreateSerializer for writes) -- there's no standalone
        # /invoice-items/ endpoint. InvoiceCreateSerializer.create()
        # supplies `invoice` itself when building each InvoiceItem, and
        # `service` is only ever set by Invoice.activate_tariff_services()
        # once a tariff line becomes a real invoice.
        read_only_fields = ["id", "invoice", "service"]

    def validate(self, attrs):
        item_type = attrs.get("item_type") or InvoiceItem.ItemType.CUSTOM
        if item_type == InvoiceItem.ItemType.PRODUCT and not attrs.get("product"):
            raise serializers.ValidationError({"product": "Select a stock item for a stock-item line."})
        if item_type == InvoiceItem.ItemType.TARIFF:
            if not attrs.get("tariff"):
                raise serializers.ValidationError({"tariff": "Select a tariff plan for a tariff-plan line."})
            if not attrs.get("period_start") or not attrs.get("period_end"):
                raise serializers.ValidationError(
                    {"period_start": "A tariff-plan line needs both a 'from' and a 'till' date."}
                )
        return attrs


class InvoiceSerializer(serializers.ModelSerializer):
    items = InvoiceItemSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    balance_due = serializers.SerializerMethodField()
    # Tells the frontend whether the "Convert to pro forma" / "Convert to
    # invoice" buttons should be shown for this record — see
    # Invoice.can_convert_to_proforma()/can_convert_to_invoice().
    can_convert_to_proforma = serializers.SerializerMethodField()
    can_convert_to_invoice = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            "id", "number", "customer", "customer_name", "status", "date_created", "date_due",
            "subtotal", "tax_total", "total", "paid_amount", "balance_due", "note", "items",
            "can_convert_to_proforma", "can_convert_to_invoice",
        ]
        read_only_fields = ["id", "number", "date_created", "subtotal", "tax_total", "total", "paid_amount"]

    def get_balance_due(self, obj):
        return obj.total - obj.paid_amount

    def get_can_convert_to_proforma(self, obj):
        return obj.can_convert_to_proforma()

    def get_can_convert_to_invoice(self, obj):
        return obj.can_convert_to_invoice()


class InvoiceCreateSerializer(serializers.ModelSerializer):
    """Accepts nested items on creation and computes totals."""

    items = InvoiceItemSerializer(many=True)
    number = serializers.CharField(read_only=True)

    class Meta:
        model = Invoice
        fields = ["id", "number", "customer", "date_due", "note", "items", "status"]

    def create(self, validated_data):
        items_data = validated_data.pop("items")
        invoice = Invoice.objects.create(**validated_data)
        for item in items_data:
            InvoiceItem.objects.create(invoice=invoice, **item)
        invoice.recalc_totals()
        # Only real invoices (not quotes/pro formas) activate a tariff
        # line's period into an actual Service -- see
        # Invoice.activate_tariff_services(). A quote/pro forma created
        # with a tariff line waits until it's converted to an invoice.
        if invoice.status not in Invoice.PRE_INVOICE_STATUSES:
            invoice.activate_tariff_services()
        return invoice


class PaymentSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    received_by_name = serializers.CharField(source="received_by.username", read_only=True, default=None)

    class Meta:
        model = Payment
        fields = [
            "id", "customer", "customer_name", "invoice", "amount", "method",
            "date", "note", "received_by", "received_by_name",
        ]
        read_only_fields = ["id", "date"]

    def validate(self, attrs):
        """A payment had no validation at all before this: `customer` and
        `invoice` were independent writable FKs, so nothing stopped a
        payment being recorded against one customer while it settled a
        DIFFERENT customer's invoice -- debiting the first customer's
        balance and flipping the second's invoice to Paid from one
        request, leaving two ledgers wrong and one customer no longer
        chased for money nobody received. bankfeeds' confirm endpoint
        passes an invoice id straight from the request body into here
        while determining the customer server-side, so that combination
        was reachable without editing anything by hand.

        Paying a quote or pro forma is refused for the reason
        recalc_totals already refuses to auto-flip one to Paid: those are
        pre-invoice documents with nothing owed on them yet. It also used
        to be a one-way trap -- a quote marked Paid fails
        can_convert_to_invoice(), so it could never become a real invoice
        again while still carrying a QUO- number and counting as a tax
        invoice in the Output VAT report.

        Amount is deliberately NOT constrained here beyond being non-zero.
        A negative "Manual Adjustment" is the existing way staff correct a
        ledger, and over-payment legitimately leaves a customer in credit.
        """
        customer = attrs.get("customer", getattr(self.instance, "customer", None))
        invoice = attrs.get("invoice", getattr(self.instance, "invoice", None))
        amount = attrs.get("amount", getattr(self.instance, "amount", None))

        if amount is not None and amount == 0:
            raise serializers.ValidationError({"amount": "A payment of zero has no effect."})

        if invoice is not None and customer is not None and invoice.customer_id != customer.pk:
            raise serializers.ValidationError({
                "invoice": "That invoice belongs to a different customer.",
            })
        if invoice is not None and invoice.status in Invoice.PRE_INVOICE_STATUSES:
            raise serializers.ValidationError({
                "invoice": (
                    f"{invoice.number} is a {invoice.get_status_display().lower()}, not an invoice. "
                    "Convert it to an invoice before recording a payment against it."
                ),
            })
        if invoice is not None and invoice.status == Invoice.Status.CANCELLED:
            raise serializers.ValidationError({
                "invoice": f"{invoice.number} has been cancelled. Record the payment against the customer instead.",
            })
        return attrs

    def update(self, instance, validated_data):
        """A payment's money fields are fixed once recorded.

        Editing them silently desynchronised the ledger: all the balance
        and paid_amount arithmetic lived in create() only, so a PATCH
        changing 1000 to 100 left the customer's balance and the invoice's
        paid_amount still reflecting 1000 forever, with the payment list
        and the ledger permanently disagreeing about the same money.

        Correcting a payment means deleting it -- which now reverses its
        ledger effect, see Payment.reverse_ledger_effect -- and recording
        the right one. Descriptive fields stay editable.
        """
        locked = {"customer", "invoice", "amount"}
        changed = [
            f for f in locked
            if f in validated_data and validated_data[f] != getattr(instance, f)
        ]
        if changed:
            raise serializers.ValidationError({
                f: "This can't be changed on a recorded payment. Delete it and record the correct one."
                for f in changed
            })
        for f in locked:
            validated_data.pop(f, None)
        return super().update(instance, validated_data)

    def create(self, validated_data):
        validated_data["received_by"] = self.context["request"].user
        payment = super().create(validated_data)
        if payment.invoice:
            payment.invoice.paid_amount += payment.amount
            if payment.invoice.paid_amount >= payment.invoice.total:
                payment.invoice.status = Invoice.Status.PAID
            payment.invoice.save()
        customer = payment.customer
        customer.balance = customer.balance - payment.amount
        customer.save()

        # Auto-send a "payment received" receipt for ANY payment (manual or
        # recurring-engine), not just ones the recurring-billing engine
        # itself creates -- gated on the same send_billing_notifications
        # flag the engine's own emails respect. Uses for_customer() (the
        # same lazy get-or-create/seed convention as everywhere else this
        # config is read) rather than requiring the customer to have opened
        # their Billing config first.
        from notifications.models import EmailTemplate
        from notifications.services import send_customer_email
        billing_config = CustomerBillingConfig.for_customer(customer)
        if billing_config.send_billing_notifications:
            send_customer_email(EmailTemplate.Key.PAYMENT_RECEIVED, customer, payment=payment)
        return payment


class CreditRequestSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    requested_by_name = serializers.CharField(source="requested_by.username", read_only=True, default=None)
    decided_by_name = serializers.CharField(source="decided_by.username", read_only=True, default=None)

    class Meta:
        model = CreditRequest
        fields = [
            "id", "customer", "customer_name", "amount", "reason", "status",
            "requested_by", "requested_by_name", "decided_by", "decided_by_name",
            "decision_note", "decided_at", "created_at",
        ]
        read_only_fields = [
            "id", "status", "requested_by", "decided_by", "decision_note", "decided_at", "created_at",
        ]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Credit amount must be greater than zero.")
        return value

    def validate_reason(self, value):
        if not value.strip():
            raise serializers.ValidationError("A reason is required for a credit request.")
        return value


class InvoiceDeletionRequestSerializer(serializers.ModelSerializer):
    # Fall back to the snapshotted display fields once `invoice` itself has
    # been deleted (after approval) -- see the model's docstring.
    invoice_number = serializers.SerializerMethodField()
    invoice_status = serializers.SerializerMethodField()
    customer_name = serializers.SerializerMethodField()
    requested_by_name = serializers.CharField(source="requested_by.username", read_only=True, default=None)
    decided_by_name = serializers.CharField(source="decided_by.username", read_only=True, default=None)

    class Meta:
        model = InvoiceDeletionRequest
        fields = [
            "id", "invoice", "invoice_number", "invoice_status", "customer_name", "reason", "status",
            "requested_by", "requested_by_name", "decided_by", "decided_by_name",
            "decision_note", "decided_at", "created_at",
        ]
        read_only_fields = [
            "id", "status", "requested_by", "decided_by", "decision_note", "decided_at", "created_at",
        ]

    def get_invoice_number(self, obj):
        if obj.invoice:
            return obj.invoice.number
        return obj.invoice_display_number or None

    def get_invoice_status(self, obj):
        return obj.invoice.status if obj.invoice else None

    def get_customer_name(self, obj):
        if obj.invoice:
            return obj.invoice.customer.full_name
        return obj.invoice_display_customer or None

    def validate_reason(self, value):
        if not value.strip():
            raise serializers.ValidationError("A reason is required to request a quote/pro forma invoice's deletion.")
        return value

    def validate_invoice(self, value):
        if value.status not in Invoice.PRE_INVOICE_STATUSES:
            raise serializers.ValidationError(
                "Only quotes and pro forma invoices can be requested for deletion this way."
            )
        if value.deletion_requests.filter(status=InvoiceDeletionRequest.Status.PENDING).exists():
            raise serializers.ValidationError("There's already a pending deletion request for this document.")
        return value


class PaymentMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentMethod
        fields = ["id", "name", "is_active"]
        read_only_fields = ["id"]


# Shared by BillingDefaultsSerializer and CustomerBillingConfigSerializer --
# mirrors RecurringBillingFieldsMixin on the model side.
_RECURRING_BILLING_FIELDS = [
    "payment_period", "payment_method", "payment_method_name", "billing_day", "use_date_of_customer_creation",
    "payment_due_days", "blocking_period_days", "deactivation_period_days", "minimum_balance",
    "auto_create_invoices", "send_billing_notifications", "auto_proforma_enabled", "proforma_day",
    "proforma_payment_period", "create_proforma_for", "reminder_enabled",
    "reminder_1_day", "reminder_2_day", "reminder_3_day",
]


# The company identity and banking block printed on a tax invoice. Kept in
# its own list because it is edited by its own form in Configs -> Billing
# and has nothing to do with the recurring-billing template above.
_COMPANY_FIELDS = [
    "company_legal_name", "company_address", "company_city", "company_postal_code",
    "company_country", "company_phone", "company_email",
    "bank_name", "bank_account_number", "bank_branch_code",
]


class BillingDefaultsSerializer(serializers.ModelSerializer):
    payment_method_name = serializers.CharField(source="payment_method.name", read_only=True, default=None)
    # The logo is written as a file (multipart PATCH) but never read back as
    # a bare MEDIA path: /media/ is behind a signature check, so a raw URL
    # would 403 in an <img>. logo_url is a signed, short-lived link, same
    # pattern as inventory.SignedAttachmentMixin. logo_name is just for
    # showing which file is currently on file.
    logo = serializers.ImageField(required=False, allow_null=True, write_only=True)
    logo_url = serializers.SerializerMethodField()
    logo_name = serializers.SerializerMethodField()

    class Meta:
        model = BillingDefaults
        fields = (
            _RECURRING_BILLING_FIELDS
            + _COMPANY_FIELDS
            + ["vat_number", "logo", "logo_url", "logo_name", "updated_at"]
        )
        read_only_fields = ["updated_at"]

    def get_logo_url(self, obj):
        if not obj.logo:
            return None
        relative_path = obj.logo.name
        url = f"{settings.MEDIA_URL}{relative_path}?sig={sign_media_path(relative_path)}"
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url

    def get_logo_name(self, obj):
        return obj.logo.name.rsplit("/", 1)[-1] if obj.logo else None


class ReminderSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReminderSettings
        fields = ["static_days", "reminder_1_enabled", "reminder_2_enabled", "reminder_3_enabled", "updated_at"]
        read_only_fields = ["updated_at"]


class SuspensionSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SuspensionSettings
        fields = ["auto_suspend_enabled", "updated_at"]
        read_only_fields = ["updated_at"]


class CustomerBillingConfigSerializer(serializers.ModelSerializer):
    payment_method_name = serializers.CharField(source="payment_method.name", read_only=True, default=None)

    class Meta:
        model = CustomerBillingConfig
        fields = _RECURRING_BILLING_FIELDS + [
            "id", "customer", "billing_enabled",
            "billing_name", "billing_street", "billing_zip", "billing_city",
            "next_billing_date", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "customer", "next_billing_date", "created_at", "updated_at"]


class RecurringBillingRunSerializer(serializers.ModelSerializer):
    partner_names = serializers.SerializerMethodField()
    triggered_by_name = serializers.CharField(source="triggered_by.username", read_only=True, default=None)

    class Meta:
        model = RecurringBillingRun
        fields = [
            "id", "run_date", "created_at", "status", "status_message", "partners", "partner_names",
            "invoices_created_count", "proforma_invoices_created_count", "reminders_sent_count",
            "suspensions_applied_count", "triggered_by", "triggered_by_name",
        ]
        read_only_fields = fields

    def get_partner_names(self, obj):
        names = list(obj.partners.values_list("name", flat=True))
        return ", ".join(names) if names else "All partners"
