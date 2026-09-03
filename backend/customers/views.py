import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.response import Response
from .models import Customer, CustomerDeletionRequest, CustomerTask, Partner
from .serializers import (
    CustomerSerializer, CustomerDeletionRequestSerializer, CustomerTaskSerializer, PartnerSerializer,
)
from .filters import CustomerFilter, CustomerTaskFilter
from accounts.permissions import IsManagement, IsStaffMember, section_permission
from config.csv_import import CSVImportMixin

HasCustomersAccess = section_permission("customers")

User = get_user_model()


def public_ip_prefetch():
    """Prefetch arguments for CustomerSerializer.public_ips.

    Without these, serialising one page of 50 customers costs 50 queries for
    their services plus one per service for its addresses, all to fill a
    single column. With them it is two extra queries for the whole page.

    Imported inside the function because billing imports customers, so a
    module-level import here is circular.
    """
    from django.db.models import Prefetch

    from billing.models import Service
    from network.models import IPAddress

    return [
        Prefetch(
            "services",
            # Terminated lines are excluded: their address has been released
            # back to the pool and belongs to whoever holds it now. Showing it
            # against the old customer would send support to the wrong house.
            queryset=Service.objects.exclude(status=Service.Status.TERMINATED).order_by("id"),
        ),
        # select_related("pool") because Service.public_ip reads
        # addr.pool.category, which would otherwise be one query per address.
        Prefetch("services__ip_addresses", queryset=IPAddress.objects.select_related("pool")),
    ]


def scope_customers_to_user(qs, user):
    """Narrow a Customer queryset to what `user` is allowed to see.

    The single implementation of customer visibility. It lives at module
    level rather than inside CustomerViewSet.get_queryset because it is
    not only list endpoints that have to honour it: bulk delete reaches
    customers by id straight from the request body, and a copy-pasted
    filter there drifted out of sync with this one and silently stopped
    scoping at all (its comment still claimed it did). Anything that
    resolves a customer from client-supplied input goes through here.

    Partner visibility restriction (see User.allowed_partners): empty
    means unrestricted, same convention as allowed_sections. Admin always
    sees everything regardless. Restricted staff still see customers with
    no partner at all (direct customers aren't "owned" by any reseller).
    A customer-role user sees only their own record; anyone with neither
    a staff role nor a customer profile sees nothing.
    """
    if user.is_staff_member:
        allowed = getattr(user, "allowed_partners", None) or []
        if allowed and user.role != user.Role.ADMIN:
            qs = qs.filter(Q(partner_id__in=allowed) | Q(partner__isnull=True))
        return qs
    customer_profile = getattr(user, "customer_profile", None)
    if customer_profile is None:
        return qs.none()
    return qs.filter(pk=customer_profile.pk)


class PartnerViewSet(viewsets.ModelViewSet):
    """Reseller partners a Customer can be tagged to (see Customer.partner).
    Any staff member can list/view partners -- needed for the Customers
    page's partner filter and to show each customer's partner name -- but
    creating, editing, or deleting a partner is a Management-level action,
    the same trust tier as approving a customer deletion."""

    serializer_class = PartnerSerializer
    queryset = Partner.objects.all().order_by("name")
    search_fields = ["name", "contact_person", "email"]
    ordering_fields = ["name", "created_at"]
    filterset_fields = ["is_active"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [permissions.IsAuthenticated(), IsStaffMember()]
        return [permissions.IsAuthenticated(), IsStaffMember(), IsManagement()]


class CustomerViewSet(CSVImportMixin, viewsets.ModelViewSet):
    serializer_class = CustomerSerializer
    filterset_class = CustomerFilter
    # Every column on the Customers page, plus the identifiers behind them.
    # It used to cover the name/contact/address group only, so typing a
    # partner name, a staff member's name, or an IP address found nothing --
    # and the page gave no hint that those were the searches it wouldn't do.
    #
    # Notes on the less obvious entries:
    #
    #   status / category / customer_type are stored as their slugs and the
    #   slugs are what the badges read ("active", "residential"), so an
    #   icontains match on the raw column matches what is on screen.
    #
    #   balance is a numeric column. Postgres can't LIKE against one, but
    #   Django casts it for icontains, so "1250" finds who owes that. A
    #   single-digit search will also hit half the balances on the page;
    #   that is the cost of the column being searchable at all.
    #
    #   the services__ entries reach across a multi-valued relation, which
    #   would return a customer once per matching service -- DRF's
    #   SearchFilter de-duplicates for exactly this case.
    search_fields = [
        "full_name", "company_name", "email", "phone", "customer_id",
        "city", "address", "zip_code", "id_number", "vat_number",
        "status", "category", "customer_type", "balance",
        "partner__name",
        "assigned_staff__username", "assigned_staff__first_name", "assigned_staff__last_name",
        # Public IP, both ways a service can hold one, plus the login that
        # goes with it -- support is usually working from one of the three.
        "services__static_ip", "services__ip_addresses__address",
        "services__radius_username",
    ]
    ordering_fields = ["created_at", "full_name", "customer_id", "category", "city", "status", "balance"]

    import_model = Customer
    import_fields = {
        # "aliases" let this same import accept a raw customer-list export
        # from the previous platform (tab-delimited, its own column names)
        # without any manual reformatting — as well as our own plain template.
        "full_name": {"required": True, "aliases": ["Full name"]},
        # The customer's payment reference. The legacy export already carries
        # their existing ID, and that is the reference those customers
        # already type on their EFTs -- so mapping it HERE, rather than only
        # burying it in notes as "Legacy ID" (which is all that used to
        # happen), is what makes bank-feed matching work for a migrated
        # customer from the first payment rather than never.
        # Blank is fine: save() generates the next CUS-######.
        "customer_id": {
            "default": "",
            "aliases": [
                "ID", "Customer ID", "Customer id", "Reference",
                "Payment reference", "Account number", "Account no",
            ],
        },
        "company_name": {"default": ""},
        "email": {"default": ""},
        "phone": {"default": ""},
        "address": {"default": "", "aliases": ["Street"]},
        "city": {"default": "", "aliases": ["City"]},
        "zip_code": {"default": ""},
        # Printed on the customer's side of a tax invoice. Both optional --
        # a residential customer usually has neither.
        "id_number": {
            "default": "",
            "aliases": ["ID number", "Id number", "Identity number", "Registration number", "Company registration"],
        },
        "vat_number": {"default": "", "aliases": ["VAT number", "Vat number", "VAT ID", "VAT no"]},
        "customer_type": {
            "default": Customer.CustomerType.INDIVIDUAL,
            "choices": Customer.CustomerType.values,
        },
        "category": {
            "default": Customer.Category.RESIDENTIAL,
            "choices": Customer.Category.values,
        },
        # Deliberately NOT aliased to the legacy export's "Status" column — that field
        # is network connectivity (Online/Offline), not account/billing
        # status, so it would be actively misleading to map it here.
        "status": {"default": Customer.Status.NEW, "choices": Customer.Status.values},
        "balance": {"type": "decimal", "default": Decimal("0"), "aliases": ["Account balance"]},
        # The customer's ORIGINAL signup date in the source system. Worth
        # mapping because Customer.created_at is auto_now_add, so without
        # this every migrated customer looks like they signed up on the day
        # of the import -- which turns the dashboard growth chart into a
        # single spike with no history behind it. Aliased to the column
        # names these exports use. Blank is fine: created_at is used then.
        "signup_date": {
            "type": "date",
            "default": None,
            "aliases": ["Date add", "Date added", "Date Added", "Signup date", "Sign-up date", "Date created", "Created"],
        },
        "assigned_staff_username": {"default": ""},
        "notes": {"default": ""},
    }

    def extra_row_validation(self, cleaned, raw_row):
        errors = []

        # Payment reference: normalise, then reject duplicates -- against
        # existing customers AND within this same file. Two customers sharing
        # a reference makes every payment for either one permanently
        # ambiguous (bankfeeds.matching refuses to guess), so it has to be
        # caught at import rather than discovered months later as a pile of
        # unmatched transactions. Upper-cased for the same reason the
        # serializer does it: Postgres uniqueness is case-sensitive but
        # matching is not.
        reference = (cleaned.get("customer_id") or "").strip().upper()
        cleaned["customer_id"] = reference
        if reference:
            if Customer.objects.filter(customer_id__iexact=reference).exists():
                errors.append(
                    f"Payment reference '{reference}' is already used by another customer"
                )
            # DRF builds a fresh viewset instance per request, so this set is
            # scoped to one upload and can't leak between imports.
            seen = getattr(self, "_seen_references", None)
            if seen is None:
                seen = self._seen_references = set()
            if reference in seen:
                errors.append(
                    f"Payment reference '{reference}' appears more than once in this file"
                )
            seen.add(reference)

        username = (cleaned.pop("assigned_staff_username", "") or "").strip()
        if username:
            staff = User.objects.filter(username=username, role__in=["admin", "support", "sales", "technician", "management", "accounts"]).first()
            if staff is None:
                errors.append(f"No staff user found with username '{username}' for 'assigned_staff_username'")
            else:
                cleaned["assigned_staff"] = staff
        else:
            cleaned["assigned_staff"] = None

        # If this looks like a legacy-platform export, preserve traceability
        # back to the source record (its internal ID, portal login, and
        # current plan aren't fields on our Customer model) in the notes
        # field rather than silently dropping them. This also lets us catch
        # the same customer appearing in more than one export file (the
        # portal login is effectively a unique customer key) and skip the
        # duplicate instead of creating a second record.
        portal_login = raw_row.get("Portal login")
        if portal_login and Customer.objects.filter(notes__icontains=f"Portal login: {portal_login}").exists():
            errors.append(f"Already imported previously (Portal login '{portal_login}' already exists)")

        if not cleaned.get("notes"):
            legacy_id = raw_row.get("ID")
            plan = raw_row.get("Internet plans")
            parts = []
            if legacy_id:
                parts.append(f"Legacy ID: {legacy_id}")
            if portal_login:
                parts.append(f"Portal login: {portal_login}")
            if plan:
                parts.append(f"Plan at import: {plan}")
            if parts:
                cleaned["notes"] = " | ".join(parts)

        return errors

    def get_permissions(self):
        if self.action == "retrieve":
            return [permissions.IsAuthenticated()]
        # list stays open to any staff member (regardless of Customers
        # section access) since Finance, Services, Scheduling, and
        # Tickets all need a customer picker for their own dropdowns --
        # only actually creating/editing/deleting/importing customer
        # records requires the Customers section itself.
        # "ids" backs the Customers page's select-all checkbox -- same
        # access tier as "list" since it's just ids off the same queryset.
        if self.action in ("list", "ids"):
            return [permissions.IsAuthenticated(), IsStaffMember()]
        return [permissions.IsAuthenticated(), IsStaffMember(), HasCustomersAccess()]

    def get_queryset(self):
        return scope_customers_to_user(
            Customer.objects.select_related("assigned_staff", "partner")
            .prefetch_related(*public_ip_prefetch())
            .all(),
            self.request.user,
        )

    def retrieve(self, request, *args, **kwargs):
        # Expire a lapsed live-view grant before showing the toggle, so staff
        # are never looking at a switch that says On for access that has
        # already timed out.
        instance = self.get_object()
        instance.expire_live_bandwidth_if_idle()
        return Response(self.get_serializer(instance).data)

    def perform_update(self, serializer):
        customer = serializer.save()
        # Turning it on starts the idle clock, so a grant nobody ever uses
        # still expires five minutes later rather than sitting on forever.
        if customer.live_bandwidth_public and not customer.live_bandwidth_last_viewed_at:
            customer.touch_live_bandwidth_view()

    def destroy(self, request, *args, **kwargs):
        # Deleting a customer cascades away every service (RADIUS logins,
        # assigned IPs), invoice, payment, credit request, ticket, and
        # email log tied to them -- too destructive for a plain DELETE
        # regardless of who's asking. The only path is
        # CustomerDeletionRequestViewSet.approve (or .bulk_delete), which
        # only Management/Admin can call. See that viewset below.
        raise MethodNotAllowed(
            "DELETE",
            detail=(
                "Customers can't be deleted directly -- submit a deletion request "
                "(POST /customer-deletion-requests/) for Management to approve."
            ),
        )

    @action(detail=False, methods=["get"], url_path="ids")
    def ids(self, request):
        """Every customer id matching the current search/filters (not just
        the current page) -- powers the Customers page's "select all
        matching these filters" checkbox, which needs to select across
        every page without pulling down a full serialized page-full of
        customer data (name/email/balance/etc.) just to get their ids.
        Reuses exactly the same filter_backends (search/filterset/
        ordering) as the normal paginated list, just without pagination."""
        queryset = self.filter_queryset(self.get_queryset())
        return Response({"ids": list(queryset.values_list("id", flat=True)), "count": queryset.count()})

    @action(detail=True, methods=["post"], url_path="regenerate-usage-link")
    def regenerate_usage_link(self, request, pk=None):
        """Issue a new usage token, killing the old link immediately.

        The usage page needs no login, so its link is a bearer credential:
        once sent it can be forwarded, screenshotted, or pasted anywhere.
        This is the revocation mechanism -- and the reason the token is a
        stored random UUID rather than a signature, since a signed token
        can't be revoked without storing something like this anyway.

        Note this breaks the link for the customer too, so they need the
        new one.
        """
        customer = self.get_object()
        customer.usage_token = uuid.uuid4()
        customer.save(update_fields=["usage_token"])
        return Response({"usage_token": str(customer.usage_token)})


class CustomerDeletionRequestViewSet(viewsets.ModelViewSet):
    """The only path by which a Customer record (and everything cascading
    from it) actually gets deleted -- see CustomerViewSet.destroy above.
    Any staff member with Customers access can submit one; only
    Management (or Admin) can approve or reject it."""

    serializer_class = CustomerDeletionRequestSerializer
    queryset = CustomerDeletionRequest.objects.select_related("customer", "requested_by", "decided_by").all()
    filterset_fields = ["customer", "status"]

    def get_queryset(self):
        """Scoped the same way the customers themselves are. A deletion
        request carries the customer's name, so an unscoped list handed a
        reseller-restricted staff member the names of customers outside
        their partners -- the thing UpcomingBlocksView's docstring in
        billing.views spells out must not happen.

        Requests whose customer has already been deleted (customer_id is
        NULL, the snapshot case the serializer handles) stay visible: the
        record is the audit trail for a deletion that already happened,
        and there is no partner left on it to filter by.
        """
        qs = super().get_queryset()
        visible = scope_customers_to_user(Customer.objects.all(), self.request.user)
        return qs.filter(Q(customer__in=visible) | Q(customer__isnull=True))

    def get_permissions(self):
        if self.action in ("approve", "reject"):
            return [permissions.IsAuthenticated(), IsManagement(), HasCustomersAccess()]
        return [permissions.IsAuthenticated(), IsStaffMember(), HasCustomersAccess()]

    def perform_create(self, serializer):
        serializer.save(requested_by=self.request.user)

    @staticmethod
    def _delete_customer(deletion_request, decided_by):
        """Shared by approve() and bulk_delete() below -- marks the
        request Approved and actually deletes the customer (cascading
        away services/invoices/payments/tickets/etc., plus the orphaned
        portal login, if any). Caller is responsible for confirming the
        request is still Pending first."""
        customer = deletion_request.customer
        deletion_request.status = CustomerDeletionRequest.Status.APPROVED
        deletion_request.decided_by = decided_by
        deletion_request.decided_at = timezone.now()
        deletion_request.save(update_fields=["status", "decided_by", "decided_at"])

        # The portal login account (if any) has no purpose once its
        # customer profile is gone -- Customer.user is SET_NULL in that
        # direction only, so deleting the Customer wouldn't otherwise
        # touch it, leaving an orphaned login behind.
        portal_user = customer.user
        customer.delete()
        if portal_user is not None:
            portal_user.delete()

    def perform_destroy(self, instance):
        # "Withdrawing" your own still-pending request -- once decided,
        # it's a permanent record (same convention as CreditRequest).
        user = self.request.user
        if instance.status != CustomerDeletionRequest.Status.PENDING and user.role not in (
            user.Role.MANAGEMENT, user.Role.ADMIN,
        ):
            raise MethodNotAllowed("DELETE", detail="This request has already been decided and can no longer be withdrawn.")
        instance.delete()

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        deletion_request = self.get_object()
        if deletion_request.status != CustomerDeletionRequest.Status.PENDING:
            return Response({"detail": "This request has already been decided."}, status=400)
        if deletion_request.customer is None:
            return Response({"detail": "This customer no longer exists."}, status=400)

        self._delete_customer(deletion_request, decided_by=request.user)
        return Response(CustomerDeletionRequestSerializer(deletion_request).data)

    @action(detail=False, methods=["post"], url_path="bulk-delete")
    def bulk_delete(self, request):
        """Powers the Customers page's "Delete selected" bulk action (see
        the select-all/per-row checkboxes there). One shared `reason` is
        recorded on a CustomerDeletionRequest per customer, same as the
        single-customer flow -- this is a batch of the exact same
        request/audit-trail records, not a bypass of it.

        Management/Admin get immediate deletion (they're the same tier
        that would otherwise just click "Approve" on each one anyway).
        Any other staff member with Customers access instead gets pending
        requests queued up for Management to review in the usual
        PendingDeletionRequestsPanel -- bulk selection doesn't grant
        anyone a bigger trust tier than they already had."""
        customer_ids = request.data.get("customer_ids")
        reason = (request.data.get("reason") or "").strip()
        if not isinstance(customer_ids, list) or not customer_ids:
            return Response({"detail": "customer_ids must be a non-empty list."}, status=400)
        if not reason:
            return Response({"detail": "A reason is required to request a customer's deletion."}, status=400)

        user = request.user
        can_decide_immediately = user.role in (user.Role.MANAGEMENT, user.Role.ADMIN)
        # Scoped through the same visibility rule a normal request would use
        # (partner visibility restrictions, etc.) -- bulk delete can't
        # reach a customer this staff member couldn't otherwise see.
        #
        # This used to read Customer.objects.filter(id__in=...) on the
        # unfiltered manager while the comment above already claimed it was
        # scoped. Because Management is the tier that deletes immediately,
        # a single reseller-restricted Management account posting a range of
        # ids could delete the entire customer base -- every partner's
        # records, cascading services, RADIUS logins, invoices and payments
        # -- without ever being able to so much as list them.
        customers_by_id = {
            c.id: c
            for c in scope_customers_to_user(Customer.objects.all(), user).filter(id__in=customer_ids)
        }

        deleted, requested, skipped = [], [], []
        for customer_id in customer_ids:
            customer = customers_by_id.get(customer_id)
            if customer is None:
                skipped.append({"id": customer_id, "error": "Not found (or not visible to you)."})
                continue
            if customer.deletion_requests.filter(status=CustomerDeletionRequest.Status.PENDING).exists():
                skipped.append({"id": customer_id, "error": "Already has a pending deletion request."})
                continue
            try:
                with transaction.atomic():
                    deletion_request = CustomerDeletionRequest.objects.create(
                        customer=customer, reason=reason, requested_by=user,
                    )
                    if can_decide_immediately:
                        self._delete_customer(deletion_request, decided_by=user)
                        deleted.append(customer_id)
                    else:
                        requested.append(customer_id)
            except Exception as exc:  # noqa: BLE001 -- one bad row shouldn't sink the whole batch
                skipped.append({"id": customer_id, "error": str(exc)})

        return Response({"deleted": deleted, "requested": requested, "skipped": skipped})

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        deletion_request = self.get_object()
        if deletion_request.status != CustomerDeletionRequest.Status.PENDING:
            return Response({"detail": "This request has already been decided."}, status=400)
        deletion_request.status = CustomerDeletionRequest.Status.REJECTED
        deletion_request.decision_note = request.data.get("decision_note", "")
        deletion_request.decided_by = request.user
        deletion_request.decided_at = timezone.now()
        deletion_request.save(update_fields=["status", "decision_note", "decided_by", "decided_at"])
        return Response(CustomerDeletionRequestSerializer(deletion_request).data)


class CustomerTaskViewSet(viewsets.ModelViewSet):
    """Internal follow-up tasks against a customer -- see
    customers.models.CustomerTask for why this isn't a Job or a Ticket.

    Staff-only, deliberately. TicketViewSet passes its section permission
    straight through for portal users because tickets ARE the customer's
    conversation; tasks are the opposite -- notes we make about chasing
    them -- so IsStaffMember is enforced here and a portal login gets
    nothing from this endpoint at all.
    """

    serializer_class = CustomerTaskSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasCustomersAccess]
    filterset_class = CustomerTaskFilter
    search_fields = ["title", "description", "customer__full_name"]
    ordering_fields = ["due_date", "created_at", "updated_at", "priority", "status"]

    def get_queryset(self):
        user = self.request.user
        qs = CustomerTask.objects.select_related("customer", "assigned_to", "created_by").all()
        # Same reseller-partner scoping as CustomerViewSet.get_queryset:
        # a staff member restricted to certain partners must not reach
        # other partners' customers through this endpoint either. Without
        # this, tasks would be a side door onto customer names the
        # Customers page deliberately hides from them.
        allowed = getattr(user, "allowed_partners", None) or []
        if allowed and user.role != user.Role.ADMIN:
            qs = qs.filter(
                Q(customer__partner_id__in=allowed) | Q(customer__partner__isnull=True)
            )
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
