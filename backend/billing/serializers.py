from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from config.media_security import sign_media_path
from customers.models import Partner
from network.models import IPPool
from radiusauth.signals import assign_specific_customer_ip, release_customer_ip, sync_service_radius
from .models import (
    Tariff, Service, Invoice, InvoiceItem, Payment, CreditRequest, InvoiceDeletionRequest,
    SuspensionExtensionRequest,
    PaymentMethod, BillingDefaults, ReminderSettings, SuspensionSettings, CustomerBillingConfig,
    RecurringBillingRun, RecurringCharge, CustomerRecurringCharge, OnceOffItem, PublicHoliday,
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
    # The id comes through fields = "__all__"; this saves every consumer
    # joining the partner list to render one column.
    partner_name = serializers.CharField(source="partner.name", read_only=True, default=None)

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


class RecurringChargeSerializer(serializers.ModelSerializer):
    # How many clients currently carry this charge -- same reasoning as
    # TariffSerializer.service_count: editing the price or unticking Active
    # reaches every one of them, so the form shows the count before anyone
    # changes a number without knowing the reach.
    client_count = serializers.SerializerMethodField()
    partner_name = serializers.CharField(source="partner.name", read_only=True, default=None)

    class Meta:
        model = RecurringCharge
        fields = "__all__"
        read_only_fields = ["id", "created_at"]

    def get_client_count(self, obj):
        annotated = getattr(obj, "client_count", None)
        return annotated if annotated is not None else obj.customer_links.values("customer").distinct().count()


class OnceOffItemSerializer(serializers.ModelSerializer):
    # How many invoice/quote lines have used it -- shown on the list so
    # nobody deletes one that's in use without knowing.
    used_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = OnceOffItem
        fields = ["id", "name", "price", "tax_rate_pct", "description", "is_active", "used_count", "created_at"]
        read_only_fields = ["id", "created_at", "used_count"]

    def validate_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Give it a name.")
        clash = OnceOffItem.objects.filter(name__iexact=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("There's already a once-off item with that name.")
        return value

    def validate_price(self, value):
        if value < 0:
            raise serializers.ValidationError("The price can't be negative.")
        return value


class CustomerRecurringChargeSerializer(serializers.ModelSerializer):
    """A client's own recurring charge (domain, email, hosting, IP rental)
    -- see models.CustomerRecurringCharge. Name/price/tax come from the
    catalog entry, live, read-only here."""

    charge_name = serializers.CharField(source="charge.name", read_only=True)
    price = serializers.DecimalField(source="charge.price", max_digits=10, decimal_places=2, read_only=True)
    tax_rate_pct = serializers.DecimalField(
        source="charge.tax_rate_pct", max_digits=5, decimal_places=2, read_only=True
    )

    # Custom pricing figures for today, incl. VAT, for the whole line
    # (13 Oct 2026). Office only where they would reveal a Rand amount up.
    price_incl_vat = serializers.SerializerMethodField()
    net_price_incl_vat = serializers.SerializerMethodField()
    discount_now_incl_vat = serializers.SerializerMethodField()
    surcharge_now_incl_vat = serializers.SerializerMethodField()
    discount_active = serializers.SerializerMethodField()

    class Meta:
        model = CustomerRecurringCharge
        fields = [
            "id", "customer", "charge", "charge_name", "price", "tax_rate_pct",
            "quantity", "start_date", "end_date", "note", "created_at",
            "discount_type", "discount_value", "discount_starts_on", "discount_ends_on", "discount_reason",
            "price_incl_vat", "net_price_incl_vat", "discount_now_incl_vat", "surcharge_now_incl_vat",
            "discount_active",
        ]
        read_only_fields = ["id", "created_at"]

    def _vat(self, obj, amount):
        rate = Decimal(obj.charge.tax_rate_pct or 0)
        return str((Decimal(amount) * (1 + rate / 100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def get_price_incl_vat(self, obj):
        return self._vat(obj, obj.base_total())

    def get_discount_now_incl_vat(self, obj):
        return self._vat(obj, obj.discount_amount_for(timezone.localdate()))

    def get_surcharge_now_incl_vat(self, obj):
        return self._vat(obj, obj.surcharge_amount_for(timezone.localdate()))

    def get_net_price_incl_vat(self, obj):
        today = timezone.localdate()
        return self._vat(obj, obj.base_total() - obj.discount_amount_for(today) + obj.surcharge_amount_for(today))

    def get_discount_active(self, obj):
        return obj.discount_applies_on(timezone.localdate())

    def to_representation(self, obj):
        data = super().to_representation(obj)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or getattr(user, "is_staff_member", False):
            return data
        # A client never sees a Rand amount up -- just the price they pay.
        data.pop("surcharge_now_incl_vat", None)
        if obj.discount_type == "surcharge":
            today = timezone.localdate()
            data["price"] = f"{obj.billed_unit_price(today):.2f}"
            data["price_incl_vat"] = data["net_price_incl_vat"]
            data.update({"discount_type": "none", "discount_value": "0.00", "discount_reason": "",
                         "discount_starts_on": None, "discount_ends_on": None, "discount_active": False,
                         "discount_now_incl_vat": "0.00"})
        return data

    PRICING_FIELDS = ("discount_type", "discount_value", "discount_starts_on", "discount_ends_on", "discount_reason")

    def _validate_pricing(self, attrs, field):
        if not any(f in attrs for f in self.PRICING_FIELDS):
            return
        request = self.context.get("request")
        if request is not None:
            from accounts.permissions import IsManagement

            if not IsManagement().has_permission(request, self.context.get("view")):
                raise serializers.ValidationError({
                    "discount_type": "Only Management or an Administrator can set custom pricing on a recurring service."
                })
        d_type = field("discount_type") or "none"
        value = Decimal(field("discount_value") or 0)
        if d_type == "none":
            attrs["discount_value"] = Decimal("0")
            return
        if value <= 0:
            raise serializers.ValidationError({"discount_value": "Enter the amount, or set the type back to none."})
        if d_type == "percentage" and value > 100:
            raise serializers.ValidationError({"discount_value": "A percentage discount can't be more than 100%."})
        charge, qty = field("charge"), field("quantity") or 1
        if d_type == "amount" and charge is not None and value > Decimal(charge.price) * Decimal(qty):
            raise serializers.ValidationError({
                "discount_value": f"That's more than the line costs (R{Decimal(charge.price) * Decimal(qty):.2f} excl. VAT)."
            })
        starts, ends = field("discount_starts_on"), field("discount_ends_on")
        if starts and ends and ends < starts:
            raise serializers.ValidationError({"discount_ends_on": "The end date can't be before the start date."})

    def validate(self, attrs):
        def field(name):
            if name in attrs:
                return attrs[name]
            return getattr(self.instance, name) if self.instance is not None else None

        customer, charge = field("customer"), field("charge")
        if self.instance is not None and "customer" in attrs and attrs["customer"] != self.instance.customer:
            raise serializers.ValidationError({"customer": "A charge can't be moved to another client."})
        # Same partner rule the catalog's ?for_customer= list applies, so a
        # stale list or a direct API call can't give a client a charge their
        # partner never offers.
        if charge is not None and customer is not None and not charge.available_to(customer.partner_id):
            raise serializers.ValidationError({
                "charge": (
                    f"“{charge.name}” belongs to {charge.partner.name}, which isn't this client's partner. "
                    "Pick one of their partner's charges, or change the charge's partner under "
                    "Configs → Tariffs → Recurring Charges."
                )
            })
        start, end = field("start_date"), field("end_date")
        if start and end and end <= start:
            raise serializers.ValidationError({"end_date": "The end date must be after the start date."})
        self._validate_pricing(attrs, field)
        return attrs


class ServiceSerializer(serializers.ModelSerializer):
    tariff_name = serializers.CharField(source="tariff.name", read_only=True)
    # The line's own partner (Rudolph, 5 Oct 2026: several partners on one
    # client) -- null = same as the client's. partner_name / effective_partner
    # are what the line is actually with: its own, else the client's.
    # (Used to be read-only and derived from the tariff.)
    partner = serializers.PrimaryKeyRelatedField(
        queryset=Partner.objects.all(), allow_null=True, required=False,
    )
    partner_name = serializers.SerializerMethodField()
    effective_partner = serializers.SerializerMethodField()
    pending_tariff_name = serializers.CharField(source="pending_tariff.name", read_only=True, default=None)
    pending_tariff_price = serializers.DecimalField(
        source="pending_tariff.price", max_digits=10, decimal_places=2, read_only=True, default=None
    )
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    price = serializers.DecimalField(source="tariff.price", max_digits=10, decimal_places=2, read_only=True)
    # The tariff's cost (13 Oct 2026), for the cost and markup on the
    # client's Services table. Null while the tariff has none.
    cost = serializers.DecimalField(
        source="tariff.cost_price", max_digits=10, decimal_places=2, read_only=True, default=None
    )
    # The same cost with VAT at the tariff's own rate, so the Services table
    # can show price and cost both incl. VAT (George, 13 Oct 2026).
    cost_incl_vat = serializers.SerializerMethodField()
    # What the line is actually billed today incl. VAT, after its discount,
    # and the discount itself incl. VAT (George, 13 Oct 2026: special
    # pricing didn't show on the Services table).
    net_price_incl_vat = serializers.SerializerMethodField()
    discount_now_incl_vat = serializers.SerializerMethodField()
    # "Rand amount up" special pricing live today, incl. VAT (13 Oct 2026).
    surcharge_now_incl_vat = serializers.SerializerMethodField()
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
    # Where this line is installed, as one line -- its own address, or the
    # client's main one (Service.use_customer_address).
    site_address_display = serializers.CharField(read_only=True)
    # What the discount is doing RIGHT NOW, rather than leaving the screen
    # to re-derive it from the type, value and two dates. A discount that
    # has not started yet, or has expired, looks identical to a live one in
    # the raw fields -- and "is this customer actually being discounted?"
    # is the only question anyone asks.
    discount_active = serializers.SerializerMethodField()
    discount_amount_now = serializers.SerializerMethodField()
    # `price` above is Tariff.price, which is the EXCLUSIVE amount --
    # billing.recurring copies it into InvoiceItem.unit_price and
    # Invoice.recalc_totals() adds tax_rate_pct on top. The customer portal
    # has to show the inclusive figure: it is the number on their invoice,
    # and a price shown to a consumer includes VAT. The rate lives on the
    # tariff rather than on the service, so it is computed here instead of
    # leaving each screen to assume 1.15 -- the day a zero-rated plan
    # exists, a hardcoded 1.15 would be wrong in every one of those places
    # at once.
    price_incl_vat = serializers.SerializerMethodField()
    # Is this line up right now (Rudolph, 8 Oct 2026)? True / False, or None
    # for a line with no RADIUS login here, or one that isn't Active. Read
    # from the map ServiceViewSet builds per page
    # (radiusauth.offline.service_connection_state); None without it.
    online = serializers.SerializerMethodField()
    last_seen_at = serializers.SerializerMethodField()
    # Package #22: what a "Takes over from" picker shows once saved.
    replaces_tariff_name = serializers.CharField(source="replaces.tariff.name", read_only=True, default=None)
    replaces_end_date = serializers.DateField(source="replaces.end_date", read_only=True, default=None)
    # The booked cancellation, if any (#203) -- see billing.service_cancel.
    cancellation = serializers.SerializerMethodField()
    # The current pause, or one booked for later (#211) -- see
    # billing.service_pause.
    pause = serializers.SerializerMethodField()

    class Meta:
        model = Service
        fields = [
            "id", "customer", "customer_name", "tariff", "tariff_name", "partner", "partner_name",
            "effective_partner",
            "price", "price_incl_vat", "cost", "cost_incl_vat", "net_price_incl_vat", "discount_now_incl_vat",
            "surcharge_now_incl_vat",
            "pending_tariff", "pending_tariff_name", "pending_tariff_price", "pending_tariff_date",
            "status", "device", "start_date", "end_date", "created_at",
            "access_device", "access_device_name", "access_device_type",
            "access_site_name", "access_detail",
            "use_customer_address", "site_address", "site_complex_or_area", "site_city", "site_zip_code",
            "site_latitude", "site_longitude",
            "site_address_display",
            "discount_type", "discount_value", "discount_starts_on", "discount_ends_on",
            "discount_reason", "discount_active", "discount_amount_now",
            "fup_threshold_gb", "fup_speed_pct", "fup_exempt",
            "radius_username", "radius_password", "radius_password_set",
            "radius_connection_type", "ip_assignment_mode", "static_ip",
            "ip_pool", "ip_pool_name", "ip_address", "assigned_ip",
            "connection_rule", "connection_rule_name", "last_radius_action",
            # Pending service that starts itself on this date (package #20).
            "activate_on",
            # The service this one takes over from -- carries over its
            # login/IP at cutover (package #22).
            "replaces", "replaces_tariff_name", "replaces_end_date",
            # True while the billing run has this line suspended for
            # non-payment. Shown, never written: only the blocking pass and
            # billing.reactivation set it (editable=False on the model).
            "suspended_for_nonpayment",
            "online", "last_seen_at",
            "cancellation",
            "pause",
        ]
        read_only_fields = [
            "id", "created_at", "discount_active", "discount_amount_now", "price_incl_vat",
            "suspended_for_nonpayment",
        ]

    def get_cancellation(self, obj):
        rows = getattr(obj, "active_cancellations", None)
        if rows is None:
            from .models import ServiceCancellation

            rows = list(obj.cancellations.filter(status=ServiceCancellation.Status.ACTIVE)[:1])
        if not rows:
            return None
        c = rows[0]
        return {
            "id": c.pk,
            "kind": c.kind,
            "kind_label": c.get_kind_display(),
            "notice_date": c.notice_date,
            "end_date": c.end_date,
            "reason": c.reason,
            "replacement_service": c.replacement_service_id,
            "replacement_name": c.replacement_service.tariff.name if c.replacement_service_id else "",
            "created_by_name": (c.created_by.get_full_name() or c.created_by.username) if c.created_by_id else "",
            "created_at": c.created_at,
            "credit_requests": [
                {"id": r.pk, "amount": str(r.amount), "status": r.status,
                 "invoice_number": r.invoice.number if r.invoice_id else None}
                for r in c.credit_requests.all()
            ],
        }

    def get_pause(self, obj):
        rows = getattr(obj, "current_pauses", None)
        if rows is None:
            from .models import ServicePause

            rows = list(
                obj.pauses.filter(status__in=[ServicePause.Status.SCHEDULED, ServicePause.Status.ACTIVE])
                .order_by("-created_at")[:1]
            )
        if not rows:
            return None
        p = rows[0]
        data = {
            "id": p.pk,
            "status": p.status,
            "pause_from": p.pause_from,
            "resume_on": p.resume_on,
            "reason": p.reason,
            "created_by_name": (p.created_by.get_full_name() or p.created_by.username) if p.created_by_id else "",
            "created_at": p.created_at,
            "credit_requests": [
                {"id": r.pk, "amount": str(r.amount), "status": r.status,
                 "invoice_number": r.invoice.number if r.invoice_id else None}
                for r in p.credit_requests.all()
            ],
        }
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is not None and not getattr(user, "is_staff_member", False):
            # A client sees that the line is paused and until when -- not
            # the internal reason or the credit workings.
            data = {k: data[k] for k in ("id", "status", "pause_from", "resume_on")}
        return data

    def _connection(self, obj):
        state = self.context.get("service_connection_map")
        return state.get(obj.pk) if state else None

    def get_online(self, obj):
        entry = self._connection(obj)
        return None if entry is None else entry["online"]

    def get_last_seen_at(self, obj):
        entry = self._connection(obj)
        return None if entry is None else entry["last_seen"]

    # Never shown to a client (George, 13 Oct 2026): our cost and any "Rand
    # amount up" uplift. A client's own API view gets the price they are
    # billed as THE price, and no trace of the uplift or of our cost.
    STAFF_ONLY_FIELDS = ("cost", "cost_incl_vat", "surcharge_now_incl_vat")

    def to_representation(self, obj):
        data = super().to_representation(obj)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        # No request = internal use (management commands, tests): unchanged.
        if user is None or getattr(user, "is_staff_member", False):
            return data
        for key in self.STAFF_ONLY_FIELDS:
            data.pop(key, None)
        if obj.discount_type == Service.DiscountType.SURCHARGE and obj.tariff_id:
            today = timezone.localdate()
            billed = obj.billed_price(today)
            data["price"] = f"{billed:.2f}"
            data["price_incl_vat"] = self._vat(obj, billed)
            data["discount_type"] = Service.DiscountType.NONE
            data["discount_value"] = "0.00"
            data["discount_reason"] = ""
            data["discount_starts_on"] = None
            data["discount_ends_on"] = None
            data["discount_active"] = False
            data["discount_amount_now"] = "0.00"
            data["discount_now_incl_vat"] = "0.00"
        return data

    def _vat(self, obj, amount):
        rate = Decimal(obj.tariff.tax_rate_pct or 0)
        return str((Decimal(amount) * (1 + rate / 100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def get_discount_now_incl_vat(self, obj):
        if not obj.tariff_id:
            return None
        return self._vat(obj, obj.discount_amount_for(obj.tariff.price, timezone.localdate()))

    def get_surcharge_now_incl_vat(self, obj):
        if not obj.tariff_id:
            return None
        return self._vat(obj, obj.surcharge_amount_for(timezone.localdate()))

    def get_net_price_incl_vat(self, obj):
        if not obj.tariff_id:
            return None
        price = Decimal(obj.tariff.price or 0)
        today = timezone.localdate()
        return self._vat(obj, price - obj.discount_amount_for(price, today) + obj.surcharge_amount_for(today))

    def get_cost_incl_vat(self, obj):
        cost = getattr(obj.tariff, "cost_price", None) if obj.tariff_id else None
        if cost is None:
            return None
        rate = Decimal(obj.tariff.tax_rate_pct or 0)
        return str((Decimal(cost) * (1 + rate / 100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def get_price_incl_vat(self, obj):
        """Today's price with VAT, as a string so no cent is lost to a float."""
        if not obj.tariff_id:
            return None
        price = Decimal(obj.tariff.price or 0)
        rate = Decimal(obj.tariff.tax_rate_pct or 0)
        # ROUND_HALF_UP to match Invoice.recalc_totals(), so the figure the
        # portal shows is the figure the invoice charges rather than one
        # that disagrees by a cent on the ties.
        return str((price * (1 + rate / 100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def get_discount_active(self, obj):
        return obj.discount_applies_on(timezone.localdate())

    def get_discount_amount_now(self, obj):
        """Rands off today's price, as a string so no cent is lost to a float."""
        price = getattr(obj.tariff, "price", None)
        return str(obj.discount_amount_for(price, timezone.localdate()))

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

    def get_effective_partner(self, obj):
        return obj.partner_id or obj.customer.partner_id

    def get_partner_name(self, obj):
        partner = obj.partner or obj.customer.partner
        return partner.name if partner else None

    def _field(self, attrs, name, default=None):
        """Value for `name` as it will be *after* this save -- from attrs
        if being changed, else whatever the instance already has (or
        `default` on create, when there's no instance yet)."""
        if name in attrs:
            return attrs[name]
        if self.instance is not None:
            return getattr(self.instance, name)
        return default

    DISCOUNT_FIELDS = (
        "discount_type", "discount_value", "discount_starts_on", "discount_ends_on", "discount_reason",
    )

    def _discount_changed(self, attrs):
        defaults = {
            "discount_type": Service.DiscountType.NONE, "discount_value": Decimal("0"),
            "discount_starts_on": None, "discount_ends_on": None, "discount_reason": "",
        }
        for f in self.DISCOUNT_FIELDS:
            if f not in attrs:
                continue
            new = attrs[f]
            old = getattr(self.instance, f) if self.instance is not None else defaults[f]
            if f == "discount_value":
                same = Decimal(str(new or 0)) == Decimal(str(old or 0))
            elif f == "discount_reason":
                same = (new or "").strip() == (old or "").strip()
            else:
                same = (new or None) == (old or None)
            if not same:
                return True
        return False

    def _validate_discount(self, attrs):
        """Discounts are a direct reduction in revenue, so they are gated
        and bounded.

        GATED: only Management/Admin may set or change one, checked through
        accounts.permissions.IsManagement rather than by re-testing role
        names here -- one definition of "management", in the place that
        already owns it. Matches how credit requests work: the people who
        can forgive money are a smaller set than the people who can edit a
        customer.

        BOUNDED: a percentage over 100, or an amount over the plan price,
        makes the invoice line net negative. That is not a discount, it is
        an unapproved credit -- and it lands in PaymentAllocation, which
        then has to allocate a payment against a negative balance. Caught
        here rather than discovered in a month-end reconciliation.
        """
        sent = [f for f in self.DISCOUNT_FIELDS if f in attrs]
        if not sent:
            return

        # Only an actual CHANGE needs Management (20 Oct 2026). The edit
        # forms send the discount fields on every save, so checking "was it
        # sent" refused every ordinary edit by support/sales staff -- even
        # one that only fixed an address.
        request = self.context.get("request")
        if request is not None and self._discount_changed(attrs):
            from accounts.permissions import IsManagement

            if not IsManagement().has_permission(request, self.context.get("view")):
                raise serializers.ValidationError({
                    "discount_type": (
                        "Only Management or an Administrator can set a discount on a service. "
                        "Ask one of them to apply it, or raise a credit request instead."
                    )
                })

        d_type = self._field(attrs, "discount_type", Service.DiscountType.NONE)
        value = self._field(attrs, "discount_value", Decimal("0")) or Decimal("0")
        starts = self._field(attrs, "discount_starts_on")
        ends = self._field(attrs, "discount_ends_on")

        if d_type == Service.DiscountType.NONE:
            # Clearing the type clears the value with it, so a stale figure
            # can't be sitting there waiting to apply itself the next time
            # somebody flips the type back on without looking at it.
            attrs["discount_value"] = Decimal("0")
            return

        if Decimal(value) <= 0:
            raise serializers.ValidationError(
                {"discount_value": "Enter how much the discount is, or set the type back to none."}
            )

        if d_type == Service.DiscountType.PERCENTAGE and Decimal(value) > 100:
            raise serializers.ValidationError(
                {"discount_value": "A percentage discount can't be more than 100%."}
            )

        if d_type == Service.DiscountType.AMOUNT:
            tariff = self._field(attrs, "tariff")
            price = getattr(tariff, "price", None)
            if price is not None and Decimal(value) > Decimal(price):
                raise serializers.ValidationError({
                    "discount_value": (
                        f"That's more than the plan costs (R{Decimal(price):.2f}). "
                        "A discount larger than the line would credit the customer, not discount them."
                    )
                })

        if starts and ends and ends < starts:
            raise serializers.ValidationError(
                {"discount_ends_on": "The end date is before the start date."}
            )

    def _validate_tariff_partner(self, attrs):
        """A customer may only be put on their own partner's plans.

        Enforced HERE and not only by filtering the dropdown, because the
        dropdown is a courtesy and this is the rule. The API is reachable
        directly, an old browser tab can hold a stale tariff list, and the
        CSV importer goes nowhere near the form. Both read
        Tariff.available_to(), so what the dropdown offers and what the
        server accepts are the same question asked twice.

        Checks the PENDING tariff too -- booking a customer onto another
        partner's plan for next month is the same mistake, just deferred,
        and it would go through silently on the day.
        """
        customer = self._field(attrs, "customer")
        own_partner = self._field(attrs, "partner")
        partner_id = own_partner.pk if own_partner else getattr(customer, "partner_id", None)

        # A partner restricted staff member may only put a line under a
        # partner they have (customers.scoping).
        request = self.context.get("request")
        user = getattr(request, "user", None)
        changed = self.instance is None or getattr(self.instance, "partner_id", None) != (
            own_partner.pk if own_partner else None
        )
        if "partner" in attrs and changed and own_partner is not None and user is not None:
            from customers.scoping import may_assign_partner

            if getattr(user, "is_staff_member", False) and not may_assign_partner(user, own_partner.pk):
                raise serializers.ValidationError({"partner": f"You don't have access to {own_partner.name}."})

        # The client's booked partner move (#20) only concerns lines that
        # follow the client's partner, not ones with a partner of their own.
        pending_partner_id = None if own_partner else getattr(customer, "pending_partner_id", None)
        pending_partner_date = getattr(customer, "pending_partner_date", None)

        for field in ("tariff", "pending_tariff"):
            # The rule is about putting a customer ON a plan. A plan merely
            # left as it is on an update is not being put -- and after a
            # partner move the old service must stay editable (package #20).
            # Changing the line's partner re-checks the plan it is on.
            if self.instance is not None and field not in attrs and not (
                "partner" in attrs and field == "tariff"
            ):
                continue
            tariff = self._field(attrs, field)
            if tariff is None or tariff.available_to(partner_id):
                continue
            # A plan of the partner the customer is booked to move to (#20):
            # allowed for a service that starts itself on/after the move, or
            # a plan change that lands on/after it.
            if pending_partner_id and pending_partner_date and tariff.available_to(pending_partner_id):
                if field == "tariff":
                    starts = self._field(attrs, "activate_on")
                    ok = (
                        self._field(attrs, "status") == Service.Status.PENDING
                        and starts is not None and starts >= pending_partner_date
                    )
                else:
                    when = self._field(attrs, "pending_tariff_date")
                    ok = when is not None and when >= pending_partner_date
                if ok:
                    continue
                raise serializers.ValidationError({
                    field: (
                        f"“{tariff.name}” is {customer.pending_partner.name}'s plan. This customer moves "
                        f"to {customer.pending_partner.name} on {pending_partner_date:%d/%m/%Y}, so it can "
                        f"only start on or after that date: choose 'On a date' on or after "
                        f"{pending_partner_date:%d/%m/%Y}."
                    )
                })
            if own_partner is not None:
                raise serializers.ValidationError({
                    field: (
                        f"“{tariff.name}” belongs to {tariff.partner.name}, but this line is with "
                        f"{own_partner.name}. Choose one of {own_partner.name}'s plans, or change the "
                        f"line's partner."
                    )
                })
            customer_partner = getattr(getattr(customer, "partner", None), "name", "no partner")
            raise serializers.ValidationError({
                field: (
                    f"“{tariff.name}” belongs to {tariff.partner.name}, but this customer is on "
                    f"{customer_partner}. Choose one of {customer_partner}'s plans, pick that partner "
                    f"for this line, or change the tariff's partner under Configs → Tariffs."
                )
            })

    def _validate_activate_on(self, attrs):
        """A service can start itself on a date only while Pending, and the
        date has to be after today (for today, set it Active). Checked only
        when the date is being set or changed. (package #20)"""
        if "activate_on" not in attrs or attrs["activate_on"] is None:
            return
        when = attrs["activate_on"]
        if self._field(attrs, "status") != Service.Status.PENDING:
            raise serializers.ValidationError(
                {"activate_on": "Only a Pending Activation service can start itself on a date."}
            )
        unchanged = self.instance is not None and self.instance.activate_on == when
        if not unchanged and when <= timezone.localdate():
            raise serializers.ValidationError(
                {"activate_on": "Pick a date after today. To start it today, set it Active."}
            )

    def _validate_replaces(self, attrs):
        """A new service may take over an existing one's login/IP at
        cutover (package #22) -- but only its own customer's, only one
        successor each, and not before the old one is due to end."""
        if "replaces" not in attrs:
            return
        old = attrs["replaces"]
        if old is None:
            return
        customer = self._field(attrs, "customer")
        if customer is not None and old.customer_id != customer.id:
            raise serializers.ValidationError({"replaces": "That service belongs to a different customer."})
        if self.instance is not None and old.pk == self.instance.pk:
            raise serializers.ValidationError({"replaces": "A service can't replace itself."})
        already = Service.objects.filter(replaces=old)
        if self.instance is not None:
            already = already.exclude(pk=self.instance.pk)
        successor = already.first()
        if successor is not None:
            raise serializers.ValidationError({
                "replaces": f"Service #{successor.pk} already takes over from this one."
            })
        starts = self._field(attrs, "activate_on")
        if old.end_date and starts and starts < old.end_date:
            raise serializers.ValidationError({
                "replaces": (
                    f"This service starts {starts:%d/%m/%Y}, before the old one's end date of "
                    f"{old.end_date:%d/%m/%Y}. Move one of the two dates so they line up."
                )
            })

    def _validate_site_address(self, attrs):
        """A line at its own address needs a street OR coordinates --
        otherwise "separate address" was ticked with nothing to show for it.
        Coordinates come as a pair, within range."""
        lat = self._field(attrs, "site_latitude")
        lng = self._field(attrs, "site_longitude")
        if (lat is None) != (lng is None):
            raise serializers.ValidationError({"site_latitude": "Enter both latitude and longitude, or neither."})
        if lat is not None and not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise serializers.ValidationError({"site_latitude": "Those coordinates are out of range."})
        if self._field(attrs, "use_customer_address", True):
            return
        if not (self._field(attrs, "site_address", "") or "").strip() and lat is None:
            raise serializers.ValidationError({
                "site_address": "Enter this service's street address or its coordinates, or tick \u201cSame as the client's main address\u201d.",
            })

    def _validate_pause_status(self, attrs):
        """Paused is set and cleared only by Pause / Resume (#211): the
        billing side reads the pause's dates, so a status picked on the edit
        form would leave it billing the wrong days."""
        if "status" not in attrs:
            return
        new = attrs["status"]
        old = self.instance.status if self.instance is not None else None
        if new == Service.Status.PAUSED and old != Service.Status.PAUSED:
            raise serializers.ValidationError(
                {"status": "Use Pause on the service to pause it (Admin / Management) — it also stops the billing."}
            )
        if old == Service.Status.PAUSED and new != Service.Status.PAUSED:
            raise serializers.ValidationError(
                {"status": "This service is paused. Use Resume on the service to switch it back on."}
            )

    def validate(self, attrs):
        self._validate_pause_status(attrs)
        self._validate_discount(attrs)
        self._validate_tariff_partner(attrs)
        self._validate_site_address(attrs)
        self._validate_activate_on(attrs)
        self._validate_replaces(attrs)
        starts, ends = self._field(attrs, "start_date"), self._field(attrs, "end_date")
        if starts and ends and ends <= starts:
            raise serializers.ValidationError({"end_date": "The end date must be after the start date."})
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
                    # A service replacing another (package #22) inherits its
                    # IP via apply_service_handover once it activates -- staff
                    # pick one explicitly only for a genuinely new address.
                    inheriting = attrs.get("replaces") is not None
                    if "ip_address" not in attrs and not has_existing and not inheriting:
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
        # A service created already Active (Starts: Now) never goes through
        # scheduled_changes, so a booked handover has to run right here
        # instead of waiting for a cutover that already happened (#22).
        if instance.replaces_id and instance.status == Service.Status.ACTIVE:
            from .scheduled_changes import apply_service_handover

            apply_service_handover(instance)
            instance.save(update_fields=["radius_username", "radius_password"])
        self._apply_ip_address_selection(instance, address_id)
        return instance


class InvoiceItemSerializer(serializers.ModelSerializer):
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True, default=None)
    tariff_name = serializers.CharField(source="tariff.name", read_only=True, default=None)
    recurring_charge_name = serializers.CharField(source="recurring_charge.name", read_only=True, default=None)
    once_off_item_name = serializers.CharField(source="once_off_item.name", read_only=True, default=None)

    class Meta:
        model = InvoiceItem
        fields = [
            "id", "invoice", "service", "item_type", "product", "product_name", "tariff", "tariff_name",
            "recurring_charge", "recurring_charge_name", "once_off_item", "once_off_item_name",
            "description", "quantity", "unit_price", "tax_rate_pct", "period_start", "period_end", "total",
            # Writable, and blank is a legitimate value meaning "decide from
            # item_type" -- see InvoiceItem.billing_kind. The quote editor
            # only needs to send it for a custom line that is one or the
            # other; everything else classifies itself.
            "billing_kind",
            # Full monthly price of a pro-rata line (16 Oct 2026) -- see
            # InvoiceItem.monthly_price. Optional; null everywhere else.
            "monthly_price",
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
        monthly_price = attrs.get("monthly_price")
        if monthly_price is not None and monthly_price < 0:
            raise serializers.ValidationError({"monthly_price": "The monthly price cannot be negative."})
        if item_type == InvoiceItem.ItemType.PRODUCT and not attrs.get("product"):
            raise serializers.ValidationError({"product": "Select a stock item for a stock-item line."})
        if item_type == InvoiceItem.ItemType.ONCE_OFF_ITEM and not attrs.get("once_off_item"):
            raise serializers.ValidationError({"once_off_item": "Select a once-off item for a once-off line."})
        if item_type == InvoiceItem.ItemType.RECURRING_CHARGE and not attrs.get("recurring_charge"):
            raise serializers.ValidationError({"recurring_charge": "Select a recurring charge for a recurring-charge line."})
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
    # Computed server-side rather than in the browser -- same reason as
    # CustomerTaskSerializer.is_overdue: "overdue" depends on today's date,
    # and status is never actually flipped to OVERDUE on a real invoice
    # (see Invoice.is_overdue and billing.overdue), so the frontend must
    # not re-derive this itself from the literal status string.
    is_overdue = serializers.BooleanField(read_only=True)
    # Tells the frontend whether the "Convert to pro forma" / "Convert to
    # invoice" buttons should be shown for this record — see
    # Invoice.can_convert_to_proforma()/can_convert_to_invoice().
    can_convert_to_proforma = serializers.SerializerMethodField()
    can_convert_to_invoice = serializers.SerializerMethodField()
    # Annotated by InvoiceViewSet: when this document was last emailed.
    last_emailed_at = serializers.DateTimeField(read_only=True, default=None)
    last_emailed_to = serializers.CharField(read_only=True, default="")

    class Meta:
        model = Invoice
        fields = [
            "id", "number", "customer", "customer_name", "status", "date_created", "date_due",
            "date_issued", "subtotal", "tax_total", "total", "paid_amount", "balance_due", "is_overdue",
            "note", "items", "can_convert_to_proforma", "can_convert_to_invoice",
            "last_emailed_at", "last_emailed_to",
        ]
        read_only_fields = [
            "id", "number", "date_created",
            # Stamped automatically the moment a document first becomes a
            # real tax invoice (see Invoice.save()) -- not staff-editable,
            # and not part of raising or editing one by hand. Exposed
            # read-only so the frontend can sort a customer's combined
            # Statement by the date each document actually counts from,
            # the same field billing.statements.movements() itself sorts
            # by, rather than date_due (a payment-terms date, not when the
            # document was issued).
            "date_issued",
            "subtotal", "tax_total", "total", "paid_amount",
            # An issued invoice cannot be moved to another customer. It
            # carries a balance debit against the customer it was issued
            # to (see Invoice.apply_balance_debit) and its line items'
            # `service` FKs point at that customer's services -- so a
            # reassignment left the debit on the first customer and the
            # services pointing at the wrong one. Reassigning is what the
            # deletion-request flow and a fresh invoice are for.
            "customer",
        ]

    def validate_status(self, value):
        """Guard the one-directional quote -> pro forma -> invoice
        lifecycle the model documents.

        `status` is writable on purpose -- staff mark an invoice cancelled
        or overdue from the list -- but nothing validated the transition,
        so a real invoice could be PATCHed back to `quote`. That was not
        merely wrong-looking: it made can_convert_to_proforma() true
        again, and converting clears `number` and reissues from the QUO
        sequence, which frees the original INV number for the next invoice
        to reuse while the customer still holds the original PDF. Number
        laundering by PATCH.

        Guarding only that direction left the forwards one wide open: a
        quote could be PATCHed straight to `unpaid` and become a live tax
        invoice without ever going through convert_to_invoice(). See the
        second check below.
        """
        if self.instance is None:
            return value
        # Going back to a quote or pro forma. Not merely wrong-looking: it
        # made can_convert_to_proforma() true again, and converting clears
        # `number` and reissues from the QUO sequence, freeing the original
        # INV number for the next invoice to reuse while the customer still
        # holds the original PDF.
        if (
            value in Invoice.PRE_INVOICE_STATUSES
            and self.instance.status not in Invoice.PRE_INVOICE_STATUSES
        ):
            raise serializers.ValidationError(
                f"{self.instance.number} is already a real invoice and can't be turned back into a "
                f"{Invoice(status=value).get_status_display().lower()}. Cancel it instead."
            )
        # Leaving a quote/pro forma by hand -- the mirror image of the
        # check above, and it was open. PATCHing a quote to `unpaid`
        # skipped convert_to_invoice() entirely, and save() only issues a
        # number when there isn't one, so the customer ended up holding a
        # live, debited, chaseable tax invoice still printed QUO-000123 --
        # numbered out of the quote sequence, which is not a valid tax
        # invoice number, while the INV sequence has no record of it. It
        # also skipped activate_tariff_services(), so the tariff the
        # customer had just accepted never became a Service: billed every
        # month for a line nobody ever switched on. Quote -> pro forma by
        # PATCH was the same story with the PF prefix.
        #
        # Cancelling is the one status edit a pre-invoice document
        # legitimately gets -- a quote that came to nothing. Every other
        # move belongs to convert_to_proforma()/convert_to_invoice(),
        # which reissue the number, debit the balance and start the
        # services.
        if (
            self.instance.status in Invoice.PRE_INVOICE_STATUSES
            and value != self.instance.status
            and value != Invoice.Status.CANCELLED
        ):
            raise serializers.ValidationError(
                f"{self.instance.number} is a {self.instance.get_status_display().lower()} -- "
                "use Convert to invoice rather than editing its status, so it gets a real invoice "
                "number and the tariff it quoted becomes a service. Cancel it instead if it came "
                "to nothing."
            )
        # Going back to draft. `draft` is outside DEBITED_STATUSES, so this
        # released the invoice's debit and silently wrote the debt off
        # while leaving the issued INV number on a document that now
        # claims not to have been issued.
        # Marking an invoice Paid by hand made it SAY paid with nothing paid
        # on it, while the balance still carried the debt -- the two halves
        # of the account then disagree for good. Paid follows money: record
        # the payment or approve a credit and it flips on its own.
        if (
            value == Invoice.Status.PAID
            and self.instance.status != Invoice.Status.PAID
            and self.instance.paid_amount < self.instance.total
        ):
            raise serializers.ValidationError(
                f"{self.instance.number} has R{self.instance.total - self.instance.paid_amount:.2f} outstanding. "
                "Record a payment or approve a credit for it -- it is marked Paid automatically once covered."
            )
        if value == Invoice.Status.DRAFT and self.instance.status != Invoice.Status.DRAFT:
            raise serializers.ValidationError(
                f"{self.instance.number} has already been issued and can't go back to draft -- "
                "that would write off what the customer owes. Cancel it instead."
            )
        return value

    def update(self, instance, validated_data):
        invoice = super().update(instance, validated_data)
        # A status edit is one of the ways an invoice starts or stops being
        # owed -- cancelling one has to take its debit back off the
        # customer's balance (and, since 5 Oct 2026, free what was
        # allocated to it -- see apply_balance_debit).
        invoice.apply_balance_debit()
        invoice.refresh_from_db()
        return invoice

    def get_balance_due(self, obj):
        return obj.total - obj.paid_amount

    def get_can_convert_to_proforma(self, obj):
        return obj.can_convert_to_proforma()

    def get_can_convert_to_invoice(self, obj):
        return obj.can_convert_to_invoice()


class PublicHolidaySerializer(serializers.ModelSerializer):
    """Configs → Billing → Public holidays (20 Oct 2026). Rows added or
    edited here are marked hand-made (seeded=False), so a later re-seed
    leaves them alone."""

    class Meta:
        model = PublicHoliday
        fields = ["id", "date", "name", "seeded"]
        read_only_fields = ["id", "seeded"]

    def validate_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Give the holiday a name.")
        return value

    def save(self, **kwargs):
        kwargs["seeded"] = False
        return super().save(**kwargs)


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
        # An invoice raised by hand is owed exactly as much as one the
        # recurring engine raised. This call is the whole of the fix for
        # the ledger drift described in Invoice.apply_balance_debit.
        invoice.apply_balance_debit()
        return invoice


class PaymentSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    received_by_name = serializers.CharField(source="received_by.username", read_only=True, default=None)
    # How much of this payment is pinned to invoices, and how much is
    # sitting as account credit. Computed from the PREFETCHED rows rather
    # than with a Sum() per payment: the list page shows a hundred at a
    # time, and a query each would be a hundred queries for two numbers.
    # PaymentViewSet.get_queryset does the prefetching.
    #
    # A payment recorded against a specific invoice credited that invoice
    # directly and wrote no allocation row -- see billing.signals -- so it
    # reads as fully unallocated here. That is why the column is only
    # offered for payments with no invoice of their own.
    allocated_amount = serializers.SerializerMethodField()
    unallocated_amount = serializers.SerializerMethodField()
    allocation_count = serializers.SerializerMethodField()

    def _allocated(self, obj):
        from decimal import Decimal

        return sum((a.amount for a in obj.allocations.all()), Decimal("0.00"))

    def get_allocated_amount(self, obj):
        return str(self._allocated(obj))

    def get_unallocated_amount(self, obj):
        return str((obj.amount or 0) - self._allocated(obj))

    def get_allocation_count(self, obj):
        return len(obj.allocations.all())

    class Meta:
        model = Payment
        fields = [
            "id", "customer", "customer_name", "invoice", "amount", "method",
            "date", "note", "received_by", "received_by_name",
            "allocated_amount", "unallocated_amount", "allocation_count",
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
        # A draft is not issued yet, so it is outside DEBITED_STATUSES and
        # carries no balance debit. Paying one reproduced the original
        # ledger drift exactly -- the payment credited the balance while
        # nothing had ever debited it -- one status value away from the
        # bug this was all meant to fix.
        if invoice is not None and invoice.status == Invoice.Status.DRAFT:
            raise serializers.ValidationError({
                "invoice": (
                    f"{invoice.number} is still a draft and has not been issued. Issue it "
                    "before recording a payment against it."
                ),
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
        from django.db.models import F

        validated_data["received_by"] = self.context["request"].user
        # Recording a payment used to read the customer, subtract, and save
        # the whole row -- three autocommit writes with no lock. Anything
        # that moved the balance between that read and that save was
        # written back out of existence: Management approves a R200 credit
        # while Accounts has the payment form open, the payment saves the
        # balance it read before the credit existed, and the customer keeps
        # R200 they were told they had been given. The API answered 201
        # either way, so nobody saw it happen.
        #
        # This was the last half of the ledger still doing it that way.
        # CreditRequestViewSet.approve, apply_balance_debit and
        # Payment.reverse_ledger_effect were all already moved to F() for
        # exactly this race; the forward payment path was not.
        #
        # The customer is touched before the invoice -- the same order
        # Payment.reverse_ledger_effect takes them in -- so recording one
        # payment while another is being deleted cannot deadlock by
        # grabbing the two rows in opposite orders.
        # "Record against this invoice" is an allocation, not a direct
        # credit (5 Oct 2026): the payment is the customer's, and
        # billing.signals settles that invoice first -- capped at what is
        # owed on it -- then the rest oldest-first. The old direct credit
        # had no cap (an overpayment stuck to one invoice) and wrote no
        # allocation row, so it could never be moved.
        preferred_invoice = validated_data.pop("invoice", None)
        with transaction.atomic():
            payment = Payment(**validated_data)
            payment._preferred_invoice_id = preferred_invoice.pk if preferred_invoice else None
            payment.save()
            customer = payment.customer
            type(customer).objects.filter(pk=payment.customer_id).update(
                balance=F("balance") - payment.amount
            )
            # The receipt below prints customer.balance, so the copy this
            # request is holding has to be re-read after an F() update --
            # otherwise every "payment received" email would quote the
            # customer's pre-payment balance.
            customer.refresh_from_db()

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
    # The visible line item this credit created on approval -- see
    # CreditRequest.create_credit_note_invoice. None for anything decided
    # before that existed and not yet backfilled.
    credit_note_number = serializers.CharField(source="credit_note.number", read_only=True, default=None)
    # The specific invoice this credit is FOR, if any -- see the `invoice`
    # field's own comment on the model. Optional: left blank, this behaves
    # exactly as it always has, a pure balance movement.
    invoice_number = serializers.CharField(source="invoice.number", read_only=True, default=None)
    # Which invoices this credit actually settled (CreditAllocation, since
    # 5 Oct 2026) -- the "Applied to" column. Empty for older credits.
    allocations_detail = serializers.SerializerMethodField()

    def get_allocations_detail(self, obj):
        return [
            {"invoice": a.invoice_id, "invoice_number": a.invoice.number, "amount": str(a.amount)}
            for a in obj.allocations.select_related("invoice").all()
        ]

    class Meta:
        model = CreditRequest
        fields = [
            "id", "customer", "customer_name", "amount", "reason", "status",
            "requested_by", "requested_by_name", "decided_by", "decided_by_name",
            "decision_note", "decided_at", "created_at",
            "credit_note", "credit_note_number", "invoice", "invoice_number",
            "allocation_tracked", "allocations_detail",
        ]
        read_only_fields = [
            "id", "status", "requested_by", "decided_by", "decision_note", "decided_at", "created_at",
            "credit_note", "credit_note_number", "invoice_number",
            "allocation_tracked", "allocations_detail",
        ]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Credit amount must be greater than zero.")
        return value

    def validate_reason(self, value):
        if not value.strip():
            raise serializers.ValidationError("A reason is required for a credit request.")
        return value

    def validate(self, attrs):
        """`invoice` is optional, but once given it has to actually be the
        kind of thing this credit could settle -- the same three checks
        PaymentSerializer.validate already makes a payment pass, for the
        same reasons: a credit aimed at a quote/pro forma isn't aimed at a
        real debt yet, a cancelled invoice is not owed, and a draft has
        never been issued so nothing debited the balance for it in the
        first place. Also refuses one customer's credit being aimed at a
        different customer's invoice -- the exact mix-up
        PaymentSerializer.validate was written to catch on payments.
        """
        customer = attrs.get("customer", getattr(self.instance, "customer", None))
        invoice = attrs.get("invoice", getattr(self.instance, "invoice", None))

        if invoice is not None and customer is not None and invoice.customer_id != customer.pk:
            raise serializers.ValidationError({
                "invoice": "That invoice belongs to a different customer.",
            })
        if invoice is not None and invoice.status in Invoice.PRE_INVOICE_STATUSES:
            raise serializers.ValidationError({
                "invoice": (
                    f"{invoice.number} is a {invoice.get_status_display().lower()}, not an invoice yet. "
                    "Convert it to an invoice before crediting it."
                ),
            })
        if invoice is not None and invoice.status == Invoice.Status.CANCELLED:
            raise serializers.ValidationError({
                "invoice": f"{invoice.number} has been cancelled -- nothing is owed on it to credit.",
            })
        if invoice is not None and invoice.status == Invoice.Status.DRAFT:
            raise serializers.ValidationError({
                "invoice": f"{invoice.number} is still a draft and has not been issued yet.",
            })
        return attrs


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
        # Scoped here as well as on the queryset. get_queryset covers
        # reading and approving, but CREATE resolves the invoice straight
        # from the id in the body, so a reseller-restricted user could
        # still POST against a competitor's quote -- and the 201 handed
        # back its number, its status and the customer's name, which is
        # exactly what the scoping was added to hide. 404-equivalent
        # wording, so this cannot be used to probe which ids exist.
        from customers.scoping import scope_by_customer

        request = self.context.get("request")
        if request is not None and value.customer_id is not None:
            visible = scope_by_customer(
                Invoice.objects.filter(pk=value.pk), request.user,
                path="customer", allow_null=False,
            )
            if not visible.exists():
                raise serializers.ValidationError("No such quote or pro forma invoice.")

        if value.status not in Invoice.PRE_INVOICE_STATUSES:
            raise serializers.ValidationError(
                "Only quotes and pro forma invoices can be requested for deletion this way."
            )
        if value.deletion_requests.filter(status=InvoiceDeletionRequest.Status.PENDING).exists():
            raise serializers.ValidationError("There's already a pending deletion request for this document.")
        return value


class SuspensionExtensionRequestSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    requested_by_name = serializers.CharField(source="requested_by.username", read_only=True, default=None)
    decided_by_name = serializers.CharField(source="decided_by.username", read_only=True, default=None)

    class Meta:
        model = SuspensionExtensionRequest
        fields = [
            "id", "customer", "customer_name", "extend_until", "reason", "status",
            "requested_by", "requested_by_name", "decided_by", "decided_by_name",
            "decision_note", "decided_at", "created_at",
        ]
        read_only_fields = [
            "id", "status", "requested_by", "decided_by", "decision_note", "decided_at", "created_at",
        ]

    def validate_reason(self, value):
        if not value.strip():
            raise serializers.ValidationError("A reason is required to request a suspension extension.")
        return value

    def validate_extend_until(self, value):
        # The cut-off date itself (20 Oct 2026): the line goes off ON it if
        # still unpaid, so today would protect nothing.
        if value <= timezone.localdate():
            raise serializers.ValidationError("Choose a date after today — the client is cut off on that date if still unpaid.")
        return value

    def validate_customer(self, value):
        from customers.models import Customer
        from customers.views import scope_customers_to_user

        request = self.context.get("request")
        if request is not None:
            visible = scope_customers_to_user(Customer.objects.filter(pk=value.pk), request.user)
            if not visible.exists():
                raise serializers.ValidationError("No such customer.")
        if value.suspension_extension_requests.filter(
            status=SuspensionExtensionRequest.Status.PENDING
        ).exists():
            raise serializers.ValidationError("There's already a pending extension request for this customer.")
        return value


# The terms a payment method carries, mirroring
# PaymentTermsMixin.TERMS_FIELDS on the model side. Listed rather than
# derived so the API surface is visible here, and asserted against the
# model in test_payment_method_terms.
_PAYMENT_TERMS_FIELDS = [
    "payment_period", "billing_day", "use_date_of_customer_creation",
    "payment_due_days", "blocking_period_days", "deactivation_period_days", "minimum_balance",
    "auto_create_invoices", "send_billing_notifications", "auto_proforma_enabled", "proforma_day",
    "proforma_payment_period", "create_proforma_for", "reminder_enabled",
    "reminder_1_day", "reminder_2_day", "reminder_3_day",
]


class PaymentMethodSerializer(serializers.ModelSerializer):
    # How many customers are currently on this method -- shown next to
    # "Apply to existing customers" so the size of that button's blast
    # radius is visible before it is pressed, not after.
    customer_count = serializers.SerializerMethodField()

    class Meta:
        model = PaymentMethod
        fields = ["id", "name", "is_active", "customer_count"] + _PAYMENT_TERMS_FIELDS
        read_only_fields = ["id", "customer_count"]

    def get_customer_count(self, obj):
        return CustomerBillingConfig.objects.filter(payment_method=obj).count()


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
        fields = ["auto_suspend_enabled", "warning_enabled", "warning_days", "updated_at"]
        read_only_fields = ["updated_at"]

    def validate_warning_days(self, value):
        # A zero notice period is a warning that arrives on the day of the
        # cut-off, which is not a warning. The engine already refuses it;
        # saying so here means somebody finds out at the settings screen
        # rather than by nobody being warned.
        if value < 1:
            raise serializers.ValidationError(
                "A warning needs at least one day's notice to be worth sending."
            )
        return value


class CustomerBillingConfigSerializer(serializers.ModelSerializer):
    payment_method_name = serializers.CharField(source="payment_method.name", read_only=True, default=None)

    class Meta:
        model = CustomerBillingConfig
        fields = _RECURRING_BILLING_FIELDS + [
            "id", "customer", "billing_enabled",
            "billing_name", "billing_street", "billing_zip", "billing_city",
            "next_billing_date", "suspension_extended_until", "created_at", "updated_at",
        ]
        # suspension_extended_until is read-only here -- it's only ever set by
        # SuspensionExtensionRequestViewSet.approve, never edited directly.
        read_only_fields = [
            "id", "customer", "next_billing_date", "suspension_extended_until", "created_at", "updated_at",
        ]


class RecurringBillingRunSerializer(serializers.ModelSerializer):
    partner_names = serializers.SerializerMethodField()
    triggered_by_name = serializers.CharField(source="triggered_by.username", read_only=True, default=None)

    class Meta:
        model = RecurringBillingRun
        fields = [
            "id", "run_date", "created_at", "status", "status_message", "partners", "partner_names",
            "invoices_created_count", "proforma_invoices_created_count", "reminders_sent_count",
            "suspension_warnings_sent_count",
            "suspensions_applied_count", "triggered_by", "triggered_by_name",
        ]
        read_only_fields = fields

    def get_partner_names(self, obj):
        names = list(obj.partners.values_list("name", flat=True))
        return ", ".join(names) if names else "All partners"


# Imported here rather than added to the module's import block so this patch
# appends only, and cannot disturb an import list that may have moved on.
from .models import BillingPreviewSnapshot  # noqa: E402


class BillingPreviewSnapshotSerializer(serializers.ModelSerializer):
    """A saved Preview as the approval queue needs it.

    `sheets` is deliberately never serialized: it is the whole CSV bundle
    (hundreds of KB on a real book) and it is meant to be downloaded through
    the export endpoint, not carried inside a JSON list of twenty snapshots.
    """

    generated_by_name = serializers.CharField(source="generated_by.username", read_only=True, default=None)
    approved_by_name = serializers.CharField(source="approved_by.username", read_only=True, default=None)
    is_approved = serializers.BooleanField(read_only=True)
    is_spent = serializers.BooleanField(read_only=True)
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = BillingPreviewSnapshot
        fields = [
            "id", "run_date", "partner_ids", "fingerprint", "engine",
            "counts", "grand_total", "created_at",
            "generated_by", "generated_by_name",
            "approved_at", "approved_by", "approved_by_name",
            "is_approved", "is_spent", "run", "download_url",
        ]
        read_only_fields = fields

    def get_download_url(self, obj):
        return "/api/recurring-billing/%s/export/" % obj.pk
