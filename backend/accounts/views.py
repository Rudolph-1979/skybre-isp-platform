import datetime
import logging
from datetime import timedelta
from django.contrib.auth.hashers import check_password
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.db.models import Sum, Count, Q
from rest_framework import generics, serializers, status, viewsets
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken
from rest_framework_simplejwt.views import TokenObtainPairView
from .models import User, TwoFactorAuth, STAFF_ROLES
from .serializers import (
    CustomTokenObtainPairSerializer,
    UserSerializer,
    StaffPermissionsSerializer,
    StaffAccountsSerializer,
)
from .permissions import IsStaffMember, IsAdmin, IsManagement
from .password_reset import send_password_reset_email, send_staff_invite_email
from . import two_factor

logger = logging.getLogger(__name__)


def _flatten_detail(value):
    """DRF wraps ValidationError dict values in lists of ErrorDetail
    (str subclasses) for i18n/multi-error support. Our 2FA error shapes
    are always single plain strings, so unwrap back to that — the
    frontend checks `response.data.code` as a plain string."""
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return str(value)


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        from audit.auth_events import record_login, record_login_failure

        attempted = str(request.data.get("username", ""))[:255]
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            record_login_failure(attempted, "Token error")
            raise InvalidToken(e.args[0]) from e
        except serializers.ValidationError as e:
            detail = e.detail
            if isinstance(detail, dict) and "code" in detail:
                code = _flatten_detail(detail["code"])
                # The password was RIGHT and the second factor was not
                # supplied or was wrong. Recorded, because somebody
                # holding a correct password is exactly the event 2FA
                # exists to stop, and it would otherwise leave no trace
                # anywhere -- the request returns a 400 that looks
                # identical to a typo in the login form.
                record_login_failure(
                    attempted,
                    "Password correct, 2FA code required"
                    if code == "two_factor_required"
                    else "Password correct, 2FA code rejected",
                )
                return Response(
                    {"code": code, "detail": _flatten_detail(detail.get("detail", ""))},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            raise
        except AuthenticationFailed:
            record_login_failure(attempted, "Wrong username or password")
            raise
        record_login(getattr(serializer, "user", None))
        return Response(serializer.validated_data, status=status.HTTP_200_OK)


class StaffListView(generics.ListAPIView):
    """Read-only list of staff/admin/technician users, for assignment
    dropdowns — scheduling jobs/shifts, ticket assignment, etc."""

    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated, IsStaffMember]

    def get_queryset(self):
        return User.objects.filter(role__in=STAFF_ROLES, is_active=True).order_by("username")


class StaffPermissionsViewSet(viewsets.ModelViewSet):
    """Management of per-user section access and reseller-partner
    visibility (see User.allowed_sections / allowed_partners). Lists every
    staff account (active and inactive, unlike StaffListView's
    dropdown-oriented queryset, since whoever's managing permissions
    should be able to see everyone).

    Section access (allowed_sections) stays Admin-only to change. Partner
    visibility (allowed_partners) is open to Management too -- the same
    trust tier as approving a customer deletion or managing Partners
    themselves -- see perform_update below for the actual split. See
    StaffPermissionsSerializer for why everything else on this endpoint is
    read-only."""

    serializer_class = StaffPermissionsSerializer
    permission_classes = [IsAuthenticated, IsManagement]
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        return User.objects.filter(role__in=STAFF_ROLES).order_by("username")

    def perform_update(self, serializer):
        user = self.request.user
        if "allowed_sections" in self.request.data and user.role != user.Role.ADMIN:
            raise serializers.ValidationError(
                {"allowed_sections": "Only an Administrator can change section access."}
            )
        serializer.save()


class StaffAccountsViewSet(viewsets.ModelViewSet):
    """Admin-only: create, edit, suspend/reactivate, and permanently
    delete staff accounts. Separate from StaffPermissionsViewSet (which
    only manages section access) and from the read-only StaffListView
    dropdown. A user can't suspend or delete their own account here —
    that's a safety net against an admin accidentally locking themselves
    out; another admin account can still do it if truly needed."""

    serializer_class = StaffAccountsSerializer
    permission_classes = [IsAuthenticated, IsAdmin]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return User.objects.filter(role__in=STAFF_ROLES).order_by("username")

    def create(self, request, *args, **kwargs):
        """Create the account, then invite them to set their own password.

        The send is deliberately AFTER the account is committed and its
        failure is reported rather than raised: an SMTP outage must not throw
        away an account that was correctly created, and the admin can retry
        from the row's "Send reset link" button. The response says which
        happened, so nobody is left assuming an email went out that didn't.
        """
        response = super().create(request, *args, **kwargs)
        user = User.objects.filter(pk=response.data.get("id")).first()
        if user is None:
            return response

        if not user.email:
            response.data["invite"] = {
                "sent": False,
                "detail": "No email address on file, so no invite was sent. They'll need the password you set.",
            }
            return response

        try:
            send_staff_invite_email(user, invited_by=request.user)
        except Exception:
            logger.exception("Failed to send invite email to new staff user %s", user.id)
            response.data["invite"] = {
                "sent": False,
                "detail": (
                    f"The account was created, but the invite to {user.email} could not be sent — "
                    "check the email settings. Use 'Send reset link' on their row to try again."
                ),
            }
        else:
            response.data["invite"] = {"sent": True, "detail": f"Invite sent to {user.email}."}
        return response

    def perform_update(self, serializer):
        instance = serializer.instance
        if instance.id == self.request.user.id and self.request.data.get("is_active") is False:
            raise serializers.ValidationError({"is_active": "You can't deactivate your own account."})
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.id == request.user.id:
            return Response({"detail": "You can't delete your own account."}, status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def send_invite(self, request, pk=None):
        """Re-send the "your account is ready, choose a password" email.

        Kept separate from send_reset_link because the two say different
        things to whoever opens them. An invite tells somebody an account has
        been set up for them and gives their username; a reset tells them
        somebody asked to change a password on an account they already know
        about. The token underneath is identical -- the wording is the point.
        """
        user = self.get_object()
        if not user.email:
            return Response(
                {"detail": "This account has no email address on file — add one under Edit first."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            send_staff_invite_email(user, invited_by=request.user)
        except Exception:
            logger.exception("Failed to send invite email to user %s", user.id)
            return Response(
                {"detail": "Could not send the email — check the server's email configuration and try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({"detail": f"Invite sent to {user.email}."})

    @action(detail=True, methods=["post"])
    def send_reset_link(self, request, pk=None):
        """Admin-triggered equivalent of the public "forgot password" flow
        -- emails this account a reset link instead of the admin having to
        invent and hand over a new password themselves."""
        user = self.get_object()
        if not user.email:
            return Response(
                {"detail": "This account has no email address on file — add one under Edit, or set a new password directly there instead."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            send_password_reset_email(user)
        except Exception:
            logger.exception("Failed to send password reset email to user %s", user.id)
            return Response(
                {"detail": "Could not send the email — check the server's email configuration and try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({"detail": f"Password reset link sent to {user.email}."})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = UserSerializer(request.user).data
        customer_profile = getattr(request.user, "customer_profile", None)
        if customer_profile is not None:
            data["customer_id"] = customer_profile.id
        return Response(data)

    def patch(self, request):
        """Self-service only: lets a staff member set their own
        `visible_partners` -- their personal default filter for which
        reseller partners' customers show up on the Customers page (see
        User.visible_partners). Handled by hand here, deliberately not
        routed through UserSerializer's write path, so this endpoint can't
        be used to edit anything else about the account (name, email,
        role, is_active, allowed_sections/allowed_partners, ...)."""
        if set(request.data.keys()) - {"visible_partners"}:
            return Response({"detail": "Only visible_partners can be updated here."}, status=400)
        if "visible_partners" not in request.data:
            return Response({"detail": "visible_partners is required."}, status=400)

        raw = request.data.get("visible_partners")
        if not isinstance(raw, list) or not all(isinstance(v, int) for v in raw):
            return Response({"visible_partners": "Must be a list of partner ids."}, status=400)

        allowed = request.user.allowed_partners or []
        if allowed and any(v not in allowed for v in raw):
            return Response({"visible_partners": "Can only include partners you have access to."}, status=400)

        request.user.visible_partners = raw
        request.user.save(update_fields=["visible_partners"])

        data = UserSerializer(request.user).data
        customer_profile = getattr(request.user, "customer_profile", None)
        if customer_profile is not None:
            data["customer_id"] = customer_profile.id
        return Response(data)


class TwoFactorStatusView(APIView):
    """Whether the current user has 2FA confirmed and active — the
    account settings page uses this to show Enable vs Disable."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        device = getattr(request.user, "two_factor", None)
        return Response({"enabled": bool(device and device.confirmed)})


class TwoFactorSetupView(APIView):
    """Starts (or restarts) 2FA setup: generates a fresh secret and QR
    code. Doesn't take effect until confirmed via TwoFactorConfirmView —
    calling this again before confirming just issues a new secret, so an
    abandoned setup can't leave stale state."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        existing = getattr(request.user, "two_factor", None)
        if existing is not None and existing.confirmed:
            return Response(
                {"detail": "Two-factor authentication is already enabled. Disable it first to reconfigure."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if existing is not None:
            existing.delete()

        secret = two_factor.generate_secret()
        device = TwoFactorAuth.objects.create(user=request.user, secret=secret)
        return Response(
            {
                "secret": secret,
                "qr_code": two_factor.qr_code_data_uri(secret, request.user.username),
            }
        )


class TwoFactorConfirmView(APIView):
    """Verifies the first code from the authenticator app and activates
    2FA. Returns one-time backup codes — shown to the user exactly once."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        device = getattr(request.user, "two_factor", None)
        if device is None or device.confirmed:
            return Response(
                {"detail": "No two-factor setup in progress. Start setup again."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        code = request.data.get("code", "")
        if not two_factor.verify_totp_code(device.secret, code):
            return Response({"detail": "Invalid code — check the time on your phone and try again."}, status=400)

        device.confirmed = True
        device.confirmed_at = timezone.now()
        device.save(update_fields=["confirmed", "confirmed_at"])
        backup_codes = two_factor.generate_backup_codes(device)
        return Response({"detail": "Two-factor authentication enabled.", "backup_codes": backup_codes})


class TwoFactorDisableView(APIView):
    """Requires the account password again — disabling 2FA is a
    security-relevant action, not something a stolen access token alone
    should be able to do without re-proving the password."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        device = getattr(request.user, "two_factor", None)
        if device is None or not device.confirmed:
            return Response({"detail": "Two-factor authentication is not enabled."}, status=400)
        if not check_password(request.data.get("password", ""), request.user.password):
            return Response({"detail": "Incorrect password."}, status=400)
        device.delete()
        return Response({"detail": "Two-factor authentication disabled."})


class DashboardSummaryView(APIView):
    """Aggregate KPI numbers for the admin dashboard landing page."""

    permission_classes = [IsAuthenticated, IsStaffMember]

    def get(self, request):
        from customers.models import Customer
        from billing.models import Invoice, Payment, Service
        from network.models import Device
        from sales.models import Lead
        from tickets.models import Ticket

        now = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        revenue_this_month = Payment.objects.filter(date__gte=month_start).aggregate(total=Sum("amount"))["total"] or 0
        outstanding = Invoice.objects.filter(status__in=["unpaid", "overdue"]).aggregate(total=Sum("total"))["total"] or 0

        return Response({
            "customers_total": Customer.objects.count(),
            "customers_active": Customer.objects.filter(status="active").count(),
            # Customers who have left. "Inactive" is what the status is called
            # in the data; "Cancelled" is what staff call it, so the tile says
            # that and the filter it links to is the same one.
            "customers_cancelled": Customer.objects.filter(status="inactive").count(),
            # Written off. The COUNT is the tile's headline, but the money is
            # the number anyone actually reacts to, so it goes in the sublabel
            # -- "3 customers" says far less than "3 customers, R14,200".
            "customers_bad_debt": Customer.objects.filter(status="bad_debt").count(),
            "customers_bad_debt_value": Customer.objects.filter(status="bad_debt").aggregate(
                total=Sum("balance")
            )["total"] or 0,
            # Of those, the ones whose last service ended in the last 30 days.
            # The total alone is a number that only ever goes up and tells you
            # nothing; recent churn is the part worth looking at.
            "customers_cancelled_recently": Customer.objects.filter(
                status="inactive",
                services__status=Service.Status.TERMINATED,
                services__end_date__gte=timezone.localdate() - datetime.timedelta(days=30),
            ).distinct().count(),
            # Open leads whose follow-up date has arrived or passed. The
            # tile exists because a follow-up nobody is reminded of is the
            # same as no follow-up: the date gets set, the day passes, and
            # the lead goes cold without anything ever saying so.
            "leads_follow_up_due": Lead.objects.filter(
                status__in=Lead.OPEN_STATUSES,
                next_follow_up__isnull=False,
                next_follow_up__lte=timezone.localdate(),
            ).count(),
            "leads_open": Lead.objects.filter(status__in=Lead.OPEN_STATUSES).count(),
            "services_active": Service.objects.filter(status="active").count(),
            "revenue_this_month": revenue_this_month,
            "outstanding_balance": outstanding,
            "invoices_unpaid": Invoice.objects.filter(status="unpaid").count(),
            "invoices_overdue": Invoice.objects.filter(status="overdue").count(),
            "devices_total": Device.objects.count(),
            "devices_online": Device.objects.filter(status="online").count(),
            "devices_offline": Device.objects.filter(status="offline").count(),
            "tickets_open": Ticket.objects.filter(status__in=["open", "pending"]).count(),
            "tickets_urgent": Ticket.objects.filter(priority="urgent").exclude(status__in=["resolved", "closed"]).count(),
        })


class CustomerGrowthView(APIView):
    """New customers per calendar month, for the dashboard growth chart.

    Aggregated in the database rather than counted in the browser on
    purpose: there are already ~1.5k customers and the list endpoint caps
    page_size at 500, so the frontend physically cannot fetch them all to
    count client-side. (The older dashboard charts got away with counting
    client-side only because they sampled a fixed 200/500 rows -- which
    quietly made those charts wrong once the data outgrew one page.)

    Buckets on COALESCE(signup_date, created_at) -- the SQL twin of
    Customer.effective_signup_date -- so a customer migrated from another
    platform lands in the month they actually signed up rather than the
    month we imported them. Keep the two in step.

    Every month in the window is returned, including empty ones: a chart
    that silently omits zero months squeezes the gaps out and misreads as
    steady growth.
    """

    permission_classes = [IsAuthenticated, IsStaffMember]

    def get(self, request):
        from django.db.models import DateField
        from django.db.models.functions import Cast, Coalesce, TruncMonth

        from customers.models import Customer

        try:
            months = int(request.query_params.get("months", 12))
        except (TypeError, ValueError):
            return Response({"detail": "months must be a whole number."}, status=400)
        months = max(1, min(months, 60))

        today = timezone.localdate()
        # First day of the window -- `months` buckets ending with the
        # current (partial) month.
        start_year, start_month = today.year, today.month - (months - 1)
        while start_month <= 0:
            start_month += 12
            start_year -= 1
        window_start = datetime.date(start_year, start_month, 1)

        signup = Coalesce("signup_date", Cast("created_at", DateField()))
        rows = (
            Customer.objects.annotate(effective_signup=signup)
            .filter(effective_signup__gte=window_start)
            .annotate(month=TruncMonth("effective_signup"))
            .values("month")
            .annotate(count=Count("id"))
        )
        counts = {r["month"]: r["count"] for r in rows if r["month"]}

        buckets = []
        year, month = start_year, start_month
        for _ in range(months):
            key = datetime.date(year, month, 1)
            buckets.append({
                "month": key.isoformat(),
                "label": key.strftime("%b %y"),
                "new_customers": counts.get(key, 0),
            })
            month += 1
            if month > 12:
                month = 1
                year += 1

        return Response({
            "months": months,
            "period_start": window_start.isoformat(),
            "period_end": today.isoformat(),
            "total_new": sum(b["new_customers"] for b in buckets),
            # How many of those have no real signup date and are therefore
            # reported on their import/creation date instead. Surfaced on
            # the dashboard so a migration spike is never mistaken for a
            # genuine month of trading.
            "estimated_from_created_at": Customer.objects.filter(
                signup_date__isnull=True, created_at__date__gte=window_start
            ).count(),
            "buckets": buckets,
        })


class HighAlertCustomersView(APIView):
    """Customers who logged more than `min_tickets - 1` tickets in a single
    calendar month at any point in the last `months` months -- the
    dashboard's "High alert customers" panel.

    Counts every department (support, billing, sales, abuse): three
    contacts of any kind in one month is a customer wanting attention,
    which is the question this panel answers.

    Reports each customer's WORST month rather than only the current one,
    plus how many separate months they breached, so a single bad month
    reads differently from a persistently unhappy account. Sorted with
    the persistent ones first (months_breached, then peak count) -- a
    customer who breached four months running is a bigger problem than
    one who filed five tickets in a single week and then went quiet.

    Respects the same partner-visibility restriction as CustomerViewSet
    (see User.allowed_partners): a reseller-scoped staff member must not
    learn the names of customers outside their partners just because
    those customers show up on a dashboard aggregate.
    """

    permission_classes = [IsAuthenticated, IsStaffMember]

    def get(self, request):
        from customers.models import Customer
        from tickets import alerts

        try:
            months = int(request.query_params.get("months", alerts.DEFAULT_MONTHS))
            min_tickets = int(request.query_params.get("min_tickets", alerts.DEFAULT_MIN_TICKETS))
        except (TypeError, ValueError):
            return Response({"detail": "months and min_tickets must be whole numbers."}, status=400)
        months = max(1, min(months, 24))
        min_tickets = max(2, min(min_tickets, 100))

        today = timezone.localdate()
        window_start = alerts.window_start(months, today)

        # Mirror CustomerViewSet.get_queryset's partner scoping.
        user = request.user
        visible = Customer.objects.all()
        allowed = getattr(user, "allowed_partners", None) or []
        if allowed and user.role != user.Role.ADMIN:
            visible = visible.filter(Q(partner_id__in=allowed) | Q(partner__isnull=True))

        # The counting itself lives in tickets.alerts, shared with the
        # Customers page's high_alert filter -- this endpoint's count is what
        # the dashboard tile shows, and that tile now clicks through to that
        # filter. Two copies of the rule would eventually disagree, and a tile
        # reading 3 that lands on a list of 5 is worse than no tile.
        flagged = alerts.high_alert_stats(visible, months, min_tickets, today)
        names = {
            c.id: c
            for c in Customer.objects.filter(id__in=flagged.keys()).only("id", "customer_id", "full_name", "status")
        }

        results = []
        for cid, entry in flagged.items():
            customer = names.get(cid)
            if customer is None:  # deleted between the two queries
                continue
            peak_month = entry["peak_month"]
            results.append({
                "customer": cid,
                "customer_ref": customer.customer_id,
                "full_name": customer.full_name,
                "status": customer.status,
                "peak_count": entry["peak_count"],
                "peak_month": peak_month.date().isoformat() if peak_month else None,
                "peak_month_label": peak_month.strftime("%b %y") if peak_month else "",
                "months_breached": entry["months_breached"],
                "total_tickets": entry["total_tickets"],
            })

        # Persistent offenders first, then heaviest single month, then a
        # stable name tiebreak so the order never jitters between loads.
        results.sort(key=lambda r: (-r["months_breached"], -r["peak_count"], r["full_name"]))

        return Response({
            "months": months,
            "min_tickets": min_tickets,
            "period_start": window_start.isoformat(),
            "period_end": today.isoformat(),
            "count": len(results),
            "results": results,
        })


class PasswordResetRequestView(APIView):
    """Public (pre-login) -- "forgot password" step one. Accepts a
    username or email and, if it matches an account that has an email on
    file, sends a reset link. Always returns the same generic response
    either way (found or not, staff or customer) so this can't be used to
    probe which usernames/emails exist."""

    permission_classes = [AllowAny]

    def post(self, request):
        identifier = (request.data.get("identifier") or "").strip()
        generic_response = {
            "detail": "If an account matches, we've emailed a password reset link to the address on file."
        }
        if not identifier:
            return Response(generic_response)

        user = User.objects.filter(Q(username__iexact=identifier) | Q(email__iexact=identifier)).first()
        if user and user.is_active and user.email:
            try:
                send_password_reset_email(user)
            except Exception:
                # Don't let an SMTP hiccup leak account existence via a
                # different-looking error, or a 500 either -- log it for an
                # admin to notice and keep the response identical.
                logger.exception("Failed to send password reset email to user %s", user.id)

        return Response(generic_response)


class PasswordResetConfirmView(APIView):
    """Public (pre-login) -- "forgot password" step two. Consumes the
    uid/token pair from the emailed link and, if still valid, sets the new
    password."""

    permission_classes = [AllowAny]

    def post(self, request):
        uid = request.data.get("uid", "")
        token = request.data.get("token", "")
        new_password = request.data.get("new_password", "")
        invalid_response = Response(
            {"detail": "This reset link is invalid or has expired — request a new one."},
            status=status.HTTP_400_BAD_REQUEST,
        )

        try:
            user_id = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=user_id)
        except (User.DoesNotExist, ValueError, TypeError, OverflowError, UnicodeDecodeError):
            return invalid_response

        if not default_token_generator.check_token(user, token):
            return invalid_response

        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as e:
            return Response({"new_password": e.messages}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(new_password)
        user.save(update_fields=["password"])
        return Response({"detail": "Your password has been reset — you can now sign in."})
