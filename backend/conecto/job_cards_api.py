"""Staff side of the Conecto job cards (7 Oct 2026): the list on a client's
profile and a job, and the card as a PDF."""
from django.http import HttpResponse
from rest_framework import permissions, serializers
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import BasePermission

from accounts.permissions import IsStaffMember, user_can_access_section
from customers.scoping import scope_by_customer

from .job_cards import JOB_TYPES, render_pdf
from .models import ConectoInternalTicket, ConectoJobCard


class HasCustomersOrScheduling(BasePermission):
    message = "You need the Customers or Scheduling section."

    def has_permission(self, request, view):
        return user_can_access_section(request.user, "customers") or user_can_access_section(
            request.user, "scheduling")


class JobCardSerializer(serializers.ModelSerializer):
    reference = serializers.CharField(read_only=True)
    customer_name = serializers.CharField(source="customer.full_name", read_only=True, default="")
    job_type_labels = serializers.SerializerMethodField()
    has_signature = serializers.SerializerMethodField()
    additional_work_ticket = serializers.SerializerMethodField()

    class Meta:
        model = ConectoJobCard
        fields = [
            "id", "reference", "conecto_job_card_id", "job", "customer", "customer_name", "client_code",
            "client_name", "technician_name", "job_types", "job_type_labels", "line_of_sight",
            "additional_work_needed", "additional_work_ticket", "client_signed_name",
            "client_unavailable_reason", "has_signature", "signature_error", "checked_in_at", "completed_at",
            "submitted_at", "received_at",
        ]

    def get_job_type_labels(self, obj):
        return [JOB_TYPES.get(t, t) for t in obj.job_types or []]

    def get_has_signature(self, obj):
        return obj.signature_png is not None and len(obj.signature_png) > 0

    def get_additional_work_ticket(self, obj):
        """Our ticket for the follow-up work, if Conecto sent it."""
        if not obj.additional_work_needed:
            return None
        link = ConectoInternalTicket.objects.select_related("ticket").filter(
            job_card_id=obj.conecto_job_card_id).first()
        if link is None and obj.additional_work_ticket_id:
            link = ConectoInternalTicket.objects.select_related("ticket").filter(
                conecto_ticket_id=obj.additional_work_ticket_id).first()
        if link is None:
            return None
        return {"id": link.ticket_id, "number": link.ticket.ticket_number}


class _Base:
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasCustomersOrScheduling]
    serializer_class = JobCardSerializer

    def get_queryset(self):
        qs = ConectoJobCard.objects.select_related("customer").defer("data")
        return scope_by_customer(qs, self.request.user, allow_null=True)


class JobCardListView(_Base, ListAPIView):
    """GET /api/job-cards/?customer=<id>|job=<id>"""

    pagination_class = None

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if str(params.get("customer", "")).isdigit():
            qs = qs.filter(customer_id=int(params["customer"]))
        if str(params.get("job", "")).isdigit():
            qs = qs.filter(job_id=int(params["job"]))
        return qs.order_by("-completed_at", "-received_at")[:200]


class JobCardPdfView(_Base, RetrieveAPIView):
    """GET /api/job-cards/<id>/pdf/"""

    def get_queryset(self):
        return scope_by_customer(ConectoJobCard.objects.select_related("customer"), self.request.user,
                                 allow_null=True)

    def retrieve(self, request, *args, **kwargs):
        card = self.get_object()
        if card.signature_url and not card.signature_png:
            from .job_cards import fetch_signature

            fetch_signature(card.pk)
            card.refresh_from_db()
        response = HttpResponse(render_pdf(card), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{card.reference} job card.pdf"'
        return response
