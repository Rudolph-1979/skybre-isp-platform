from rest_framework import serializers
from .models import Customer, CustomerDeletionRequest, CustomerTask, Partner


class PartnerSerializer(serializers.ModelSerializer):
    customer_count = serializers.SerializerMethodField()

    class Meta:
        model = Partner
        fields = [
            "id", "name", "contact_person", "email", "phone",
            "commission_rate", "notes", "is_active", "created_at", "customer_count",
        ]
        read_only_fields = ["id", "created_at"]

    def get_customer_count(self, obj):
        return obj.customers.count()

    def validate_name(self, value):
        value = value.strip()
        qs = Partner.objects.filter(name__iexact=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A partner with this name already exists.")
        return value


class CustomerSerializer(serializers.ModelSerializer):
    assigned_staff_name = serializers.CharField(source="assigned_staff.username", read_only=True, default=None)
    partner_name = serializers.CharField(source="partner.name", read_only=True, default=None)
    # The no-login usage link to send this customer. Read-only: it changes
    # only through the regenerate-usage-link action, so an ordinary save
    # can't alter it by accident and silently break a link the customer has
    # already bookmarked.
    usage_token = serializers.UUIDField(read_only=True)
    # The customer's payment reference, now writable (it used to be read-only)
    # so a customer migrated from another system can keep the reference they
    # already put on their EFTs -- which is what bank-feed matching looks for.
    #
    # Optional: leave it out or send it blank and the model generates the next
    # CUS-######. On an UPDATE, blank means "leave it alone" rather than
    # "clear it", so a PATCH that doesn't care about the reference can't wipe
    # one the customer is actively using.
    customer_id = serializers.CharField(
        required=False, allow_blank=True, max_length=20, label="Payment reference"
    )
    # Every public address this customer's services are currently handing out.
    #
    # A list rather than one value, because a customer can hold more than one
    # line and choosing which of them to show would be arbitrary. The question
    # this column exists to answer -- "who is 102.23.154.3?" -- needs all of
    # them or it silently misses the second line.
    #
    # Cheap only because CustomerViewSet.get_queryset prefetches the services
    # and their pool addresses; see the note there before using this anywhere
    # that doesn't.
    public_ips = serializers.SerializerMethodField()

    class Meta:
        model = Customer
        fields = [
            "id", "customer_id", "customer_type", "category", "full_name", "company_name",
            "email", "phone", "address", "city", "zip_code", "id_number", "vat_number",
            "status", "balance", "public_ips", "live_bandwidth_public",
            "live_bandwidth_last_viewed_at",
            "assigned_staff", "assigned_staff_name", "partner", "partner_name",
            "notes", "signup_date", "created_at", "updated_at", "user", "usage_token",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "usage_token", "live_bandwidth_last_viewed_at"]

    def get_public_ips(self, obj):
        # dict.fromkeys rather than set(): two services on the same manual
        # static IP is a misconfiguration, but the column should show it once,
        # in the order the services are listed, not in a random one.
        addresses = (service.public_ip for service in obj.services.all())
        return list(dict.fromkeys(a for a in addresses if a))

    def validate_customer_id(self, value):
        """Normalise to trimmed upper case, and reject a duplicate.

        Upper-casing is not cosmetic. Postgres unique constraints are
        case-SENSITIVE, so "sky1" and "SKY1" could both exist -- while
        bankfeeds.matching compares references case-insensitively and would
        see them as the same reference, making every payment for either
        customer permanently ambiguous and therefore unmatched. Storing one
        canonical form makes that impossible.
        """
        value = (value or "").strip().upper()
        if not value:
            return ""
        clash = Customer.objects.filter(customer_id__iexact=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                f"Another customer already uses the reference '{value}'. "
                "References must be unique so bank payments can be matched to one customer."
            )
        return value

    def update(self, instance, validated_data):
        # Blank on update means "unchanged", not "clear it". Clearing would
        # make save() mint a brand-new CUS-###### and quietly orphan every
        # future EFT the customer sends with their old reference.
        if "customer_id" in validated_data and not validated_data["customer_id"]:
            validated_data.pop("customer_id")
        return super().update(instance, validated_data)


class CustomerDeletionRequestSerializer(serializers.ModelSerializer):
    # Falls back to the snapshotted display fields once `customer` itself
    # has been deleted (after approval) -- see the model's docstring.
    customer_name = serializers.SerializerMethodField()
    requested_by_name = serializers.CharField(source="requested_by.username", read_only=True, default=None)
    decided_by_name = serializers.CharField(source="decided_by.username", read_only=True, default=None)

    class Meta:
        model = CustomerDeletionRequest
        fields = [
            "id", "customer", "customer_name", "reason", "status",
            "requested_by", "requested_by_name", "decided_by", "decided_by_name",
            "decision_note", "decided_at", "created_at",
        ]
        read_only_fields = [
            "id", "status", "requested_by", "decided_by", "decision_note", "decided_at", "created_at",
        ]

    def get_customer_name(self, obj):
        if obj.customer:
            return obj.customer.full_name
        return obj.customer_display_name or obj.customer_display_id or None

    def validate_reason(self, value):
        if not value.strip():
            raise serializers.ValidationError("A reason is required to request a customer's deletion.")
        return value

    def validate_customer(self, value):
        if value.deletion_requests.filter(status=CustomerDeletionRequest.Status.PENDING).exists():
            raise serializers.ValidationError("There's already a pending deletion request for this customer.")
        return value


class CustomerTaskSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    assigned_to_name = serializers.CharField(source="assigned_to.username", read_only=True, default=None)
    created_by_name = serializers.CharField(source="created_by.username", read_only=True, default=None)
    # Computed server-side rather than in the browser: "overdue" depends on
    # today's date in OUR timezone, and a customer's laptop set to another
    # one would otherwise disagree with the same list on the next desk.
    is_overdue = serializers.BooleanField(read_only=True)
    is_outstanding = serializers.BooleanField(read_only=True)

    class Meta:
        model = CustomerTask
        fields = [
            "id", "customer", "customer_name", "title", "description",
            "status", "priority", "due_date",
            "assigned_to", "assigned_to_name", "created_by", "created_by_name",
            "is_overdue", "is_outstanding",
            "completed_at", "created_at", "updated_at",
        ]
        # created_by is stamped from the request (see the viewset), not
        # accepted from the client -- otherwise anyone could file a task
        # under someone else's name.
        read_only_fields = ["id", "created_by", "completed_at", "created_at", "updated_at"]

    def validate_title(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Give the task a title — it's what shows in the list.")
        return value
