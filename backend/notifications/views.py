from django.shortcuts import get_object_or_404
from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsStaffMember
from billing.models import Invoice
from customers.models import Customer

from . import services
from .models import EmailTemplate, EmailLog
from .serializers import EmailTemplateSerializer, EmailLogSerializer

# Bulk sends don't support the "invoice" template — it's inherently tied to
# one specific invoice, which doesn't make sense picked once across many
# customers. Keep this in sync with BulkEmailView.post()'s validation.
BULK_ELIGIBLE_KEYS = [
    k for k in EmailTemplate.Key.values if k != EmailTemplate.Key.INVOICE
]


class EmailTemplateViewSet(viewsets.ModelViewSet):
    """The 5 fixed template slots — list/retrieve/update only (no create or
    delete; rows are seeded once by a data migration)."""

    queryset = EmailTemplate.objects.all()
    serializer_class = EmailTemplateSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember]
    http_method_names = ["get", "patch", "put", "head", "options"]


class EmailLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = EmailLog.objects.select_related("customer", "sent_by").all()
    serializer_class = EmailLogSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember]
    filterset_fields = ["customer", "template_key", "status", "batch_id"]


class EmailPreviewView(APIView):
    """Renders (but does not send) a template against a real customer, so
    the UI can show staff exactly what will go out before they click Send."""

    permission_classes = [permissions.IsAuthenticated, IsStaffMember]

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
        elif template_key == EmailTemplate.Key.INVOICE:
            return Response(
                {"detail": "An invoice must be selected to preview an Invoice email."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            subject, body = services.preview_template(template_key, customer, invoice)
        except EmailTemplate.DoesNotExist:
            return Response({"detail": "That template hasn't been set up yet."}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "subject": subject,
            "body_html": body,
            "will_attach_pdf": template_key in (EmailTemplate.Key.INVOICE, EmailTemplate.Key.STATEMENT),
        })


class SendCustomerEmailView(APIView):
    """Sends one templated email to one customer right away (synchronous —
    a single SMTP round-trip is fast enough not to need the background
    thread bulk sending uses)."""

    permission_classes = [permissions.IsAuthenticated, IsStaffMember]

    def post(self, request, customer_id):
        template_key = request.data.get("template_key")
        invoice_id = request.data.get("invoice")

        if template_key not in EmailTemplate.Key.values:
            return Response({"detail": "Invalid template_key."}, status=status.HTTP_400_BAD_REQUEST)
        customer = get_object_or_404(Customer, pk=customer_id)

        invoice = None
        if invoice_id:
            invoice = get_object_or_404(Invoice, pk=invoice_id, customer=customer)
        elif template_key == EmailTemplate.Key.INVOICE:
            return Response(
                {"detail": "An invoice must be selected to send an Invoice email."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        log = services.send_customer_email(
            template_key, customer, sent_by_id=request.user.id, invoice=invoice,
        )
        serializer = EmailLogSerializer(log)
        http_status = status.HTTP_200_OK if log.status == EmailLog.Status.SENT else status.HTTP_502_BAD_GATEWAY
        return Response(serializer.data, status=http_status)


class BulkEmailView(APIView):
    """Kicks off a bulk send to an explicit list of customer IDs (the
    frontend narrows this list using the same filters as the Customers page,
    then staff checkbox-select who actually receives it). Runs in a
    background thread — see services.send_bulk_emails — so this returns
    immediately with a batch_id the UI can use to poll /email-logs/."""

    permission_classes = [permissions.IsAuthenticated, IsStaffMember]

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
