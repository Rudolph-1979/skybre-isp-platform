from django.core.mail import EmailMessage
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdmin, IsStaffMember, section_permission
from billing.models import Invoice
from customers.models import Customer

from . import services
from .email_settings import get_email_config, get_email_connection
from .models import EmailTemplate, EmailLog, EmailSettings
from .serializers import EmailTemplateSerializer, EmailLogSerializer, EmailSettingsSerializer

HasConfigsAccess = section_permission("configs")
HasBulkEmailAccess = section_permission("bulk_email")
# EmailPreviewView/SendCustomerEmailView are invoked from the Customers
# section (CustomerDetailPage's "send this document" flow), not from Bulk
# Email -- gated separately so a Customers-permitted-but-not-Bulk-Email
# staff member can still email an individual customer their invoice.
HasCustomersAccess = section_permission("customers")

# These three templates are each inherently tied to one specific document
# (an invoice, quote, or pro forma) — bulk sending doesn't make sense for
# them since a batch of customers won't all share the same document. Keep
# this in sync with BulkEmailView.post()'s validation and the `invoice=`
# requirement in EmailPreviewView/SendCustomerEmailView below.
DOCUMENT_TEMPLATE_KEYS = (
    EmailTemplate.Key.INVOICE,
    EmailTemplate.Key.QUOTE,
    EmailTemplate.Key.PROFORMA,
)
BULK_ELIGIBLE_KEYS = [
    k for k in EmailTemplate.Key.values if k not in DOCUMENT_TEMPLATE_KEYS
]


class EmailTemplateViewSet(viewsets.ModelViewSet):
    """The 5 fixed template slots — list/retrieve/update only (no create or
    delete; rows are seeded once by a data migration)."""

    queryset = EmailTemplate.objects.all()
    serializer_class = EmailTemplateSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasConfigsAccess]
    http_method_names = ["get", "patch", "put", "head", "options"]


class EmailLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = EmailLog.objects.select_related("customer", "sent_by").all()
    serializer_class = EmailLogSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasBulkEmailAccess]
    filterset_fields = ["customer", "template_key", "status", "batch_id"]


class EmailPreviewView(APIView):
    """Renders (but does not send) a template against a real customer, so
    the UI can show staff exactly what will go out before they click Send."""

    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasCustomersAccess]

    def post(self, request):
        template_key = request.data.get("template_key")
        customer_id = request.data.get("customer")
        invoice_id = request.data.get("invoice")

        if template_key not in EmailTemplate.Key.values:
            return Response({"detail": "Invalid template_key."}, status=status.HTTP_400_BAD_REQUEST)
        customer = get_object_or_404(Customer, pk=customer_id)

        invoice = None
        if invoice_id:
            invoice = get_object_or_404(Invoice, pk=invoice_id, customer=customer)
        elif template_key in DOCUMENT_TEMPLATE_KEYS:
            return Response(
                {"detail": "A document must be selected to preview this email."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            subject, body = services.preview_template(template_key, customer, invoice)
        except EmailTemplate.DoesNotExist:
            return Response({"detail": "That template hasn't been set up yet."}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "subject": subject,
            "body_html": body,
            "will_attach_pdf": template_key in (*DOCUMENT_TEMPLATE_KEYS, EmailTemplate.Key.STATEMENT),
        })


class SendCustomerEmailView(APIView):
    """Sends one templated email to one customer right away (synchronous —
    a single SMTP round-trip is fast enough not to need the background
    thread bulk sending uses)."""

    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasCustomersAccess]

    def post(self, request, customer_id):
        template_key = request.data.get("template_key")
        invoice_id = request.data.get("invoice")

        if template_key not in EmailTemplate.Key.values:
            return Response({"detail": "Invalid template_key."}, status=status.HTTP_400_BAD_REQUEST)
        customer = get_object_or_404(Customer, pk=customer_id)

        invoice = None
        if invoice_id:
            invoice = get_object_or_404(Invoice, pk=invoice_id, customer=customer)
        elif template_key in DOCUMENT_TEMPLATE_KEYS:
            return Response(
                {"detail": "A document must be selected to send this email."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        log = services.send_customer_email(
            template_key, customer, sent_by_id=request.user.id, invoice=invoice,
        )
        serializer = EmailLogSerializer(log)
        http_status = status.HTTP_200_OK if log.status == EmailLog.Status.SENT else status.HTTP_502_BAD_GATEWAY
        return Response(serializer.data, status=http_status)


class _DocumentPdfMixin:
    """Shared plumbing for the two PDF endpoints below.

    Both are readable by staff with Customers access AND by the customer the
    document belongs to (the portal shows customers their own invoices). That
    dual audience is the whole reason these live here rather than on
    billing.InvoiceViewSet, which is Finance-gated and would 403 a customer.

    The PDF itself is built by services._build_attachment -- deliberately the
    exact same call the emailer makes, not a second rendering path that
    happens to look similar. That is what makes "Preview" mean something:
    the document on screen is the document that gets sent, and a layout fix
    can never land in one and miss the other. (The bytes differ run to run
    only because a PDF embeds its own creation timestamp.)
    """

    permission_classes = [permissions.IsAuthenticated]

    def _own_customer(self, request):
        """The Customer row this user IS, or None for staff/unlinked users."""
        return getattr(request.user, "customer_profile", None)

    def _deny_unless_permitted(self, request, customer):
        user = request.user
        if user.is_staff_member:
            # Same section gate as the rest of the customer document flow --
            # see HasCustomersAccess above.
            if not HasCustomersAccess().has_permission(request, self):
                return Response({"detail": "You do not have access to customer documents."}, status=status.HTTP_403_FORBIDDEN)
            return None
        own = self._own_customer(request)
        if own is not None and own.pk == customer.pk:
            return None
        # Deliberately 404, not 403: a customer probing other ids should not
        # be able to tell an existing document from a missing one.
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    def _inline_pdf(self, filename, pdf_bytes):
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        # `inline`, not `attachment`: this is a preview, so the browser should
        # render it in the viewer/iframe rather than drop it in Downloads. The
        # frontend still offers an explicit Download, which saves the same
        # blob it is already showing.
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        return response


class InvoicePdfView(_DocumentPdfMixin, APIView):
    """The PDF for one invoice, quote or pro forma.

    Which of the three it is comes from the row's own status rather than a
    query parameter -- a caller cannot ask for a quote to be rendered as a
    tax invoice, and the heading/wording follows the document's real state.
    """

    def get(self, request, pk):
        invoice = get_object_or_404(Invoice, pk=pk)
        denied = self._deny_unless_permitted(request, invoice.customer)
        if denied is not None:
            return denied

        key_by_status = {
            Invoice.Status.QUOTE: EmailTemplate.Key.QUOTE,
            Invoice.Status.PROFORMA: EmailTemplate.Key.PROFORMA,
        }
        template_key = key_by_status.get(invoice.status, EmailTemplate.Key.INVOICE)
        filename, pdf_bytes, _ = services._build_attachment(template_key, invoice.customer, invoice=invoice)
        return self._inline_pdf(filename, pdf_bytes)


class CustomerStatementPdfView(_DocumentPdfMixin, APIView):
    """The statement PDF for one customer -- the same document the statement
    email attaches, so it excludes quotes and pro formas for the reason given
    in services._build_attachment: a statement is a record of what was
    actually billed, not a mix of billed and merely-quoted amounts."""

    def get(self, request, customer_id):
        customer = get_object_or_404(Customer, pk=customer_id)
        denied = self._deny_unless_permitted(request, customer)
        if denied is not None:
            return denied

        filename, pdf_bytes, _ = services._build_attachment(EmailTemplate.Key.STATEMENT, customer)
        return self._inline_pdf(filename, pdf_bytes)


class BulkEmailView(APIView):
    """Kicks off a bulk send to an explicit list of customer IDs (the
    frontend narrows this list using the same filters as the Customers page,
    then staff checkbox-select who actually receives it). Runs in a
    background thread — see services.send_bulk_emails — so this returns
    immediately with a batch_id the UI can use to poll /email-logs/."""

    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasBulkEmailAccess]

    def post(self, request):
        template_key = request.data.get("template_key")
        customer_ids = request.data.get("customer_ids") or []

        if template_key not in BULK_ELIGIBLE_KEYS:
            return Response(
                {"detail": "That template can't be used for bulk sending."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not isinstance(customer_ids, list) or not customer_ids:
            return Response({"detail": "Select at least one customer."}, status=status.HTTP_400_BAD_REQUEST)

        valid_ids = list(
            Customer.objects.filter(pk__in=customer_ids).values_list("pk", flat=True)
        )
        if not valid_ids:
            return Response({"detail": "None of the selected customers could be found."}, status=status.HTTP_400_BAD_REQUEST)

        batch_id, queued_count = services.send_bulk_emails(
            template_key, valid_ids, sent_by_id=request.user.id,
        )
        return Response({"batch_id": batch_id, "queued_count": queued_count})


class EmailSettingsView(APIView):
    """Admin-only: view and update the platform's outgoing-email (SMTP)
    configuration from Configs -> Email Settings. A GET/PATCH pair against
    a singleton row rather than a ModelViewSet, since there's only ever
    one of these — see EmailSettings.load()."""

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get(self, request):
        return Response(EmailSettingsSerializer(EmailSettings.load()).data)

    def patch(self, request):
        serializer = EmailSettingsSerializer(EmailSettings.load(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class EmailSettingsTestView(APIView):
    """Admin-only: sends a real test email using the currently saved SMTP
    configuration, so an admin can confirm a change actually works before
    relying on it for real customer mail."""

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def post(self, request):
        recipient = (request.data.get("recipient") or request.user.email or "").strip()
        if not recipient:
            return Response(
                {"detail": "No recipient address on file for your account — add one first, or provide one."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        cfg = get_email_config()
        try:
            message = EmailMessage(
                subject=f"{cfg['company_name']} — test email",
                body=(
                    "This is a test email from your ISP platform's Configs -> Email Settings page. "
                    "If you received this, outgoing email is configured correctly."
                ),
                from_email=cfg["from_email"],
                to=[recipient],
                connection=get_email_connection(),
            )
            message.send(fail_silently=False)
        except Exception as exc:  # noqa: BLE001 — surface the actual SMTP error to the admin testing it
            return Response({"detail": f"Could not send the test email: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"detail": f"Test email sent to {recipient}."})
