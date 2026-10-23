from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from accounts.permissions import IsStaffMember, section_permission
from audit.middleware import client_ip
from customers.scoping import scope_by_customer, scope_customers_to_user

from . import services
from .models import Contract, ContractSettings, ContractTemplate
from .serializers import (
    ContractDetailSerializer, ContractSerializer, ContractSettingsSerializer, ContractTemplateSerializer,
)
from .services import ContractError

HasConfigsAccess = section_permission("configs")
HasCustomersAccess = section_permission("customers")


def _pdf(data, filename, inline=True):
    response = HttpResponse(bytes(data), content_type="application/pdf")
    safe = filename.replace('"', "'")
    response["Content-Disposition"] = f'{"inline" if inline else "attachment"}; filename="{safe}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _error(exc, code=status.HTTP_400_BAD_REQUEST):
    return Response({"detail": str(exc)}, status=code)


class ContractTemplateViewSet(viewsets.ModelViewSet):
    """Configs -> Contracts. Anyone sending a contract can list the
    templates; only Configs can change them."""

    serializer_class = ContractTemplateSerializer
    pagination_class = None

    def get_queryset(self):
        return ContractTemplate.objects.annotate(contract_count=Count("contracts")).order_by("name")

    def get_permissions(self):
        if self.action in ("list", "retrieve", "placeholders"):
            return [permissions.IsAuthenticated(), IsStaffMember()]
        return [permissions.IsAuthenticated(), IsStaffMember(), HasConfigsAccess()]

    def destroy(self, request, *args, **kwargs):
        template = self.get_object()
        if template.contract_count:
            return _error(f"{template.contract_count} contract(s) were made from this template. "
                          "Switch it off instead.")
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=["get"])
    def placeholders(self, request):
        return Response([{"key": k, "label": label} for k, label in services.PLACEHOLDERS])

    @action(detail=True, methods=["get"])
    def preview(self, request, pk=None):
        """The template filled in for one client (?customer=) or sample
        details, as the PDF the client would get. Nothing is saved."""
        from customers.models import Customer

        template = self.get_object()
        customer = None
        if request.query_params.get("customer"):
            customer = scope_customers_to_user(Customer.objects.all(), request.user).filter(
                pk=request.query_params["customer"]).first()
        if customer is None:
            customer = Customer(full_name="Sample Client", customer_id="SWF0000", email="client@example.com",
                                phone="082 000 0000", address="1 Sample Street", city="Kroonstad",
                                id_number="8001015009087")
        contract = Contract(customer=customer, title=template.title, kind=Contract.Kind.TEMPLATE)
        try:
            contract.body_html = services.fill(template.body_html,
                                               services.merge_context(customer, None, "CTR-preview"))
            pdf = services.render_contract_pdf(contract)
        except ContractError as exc:
            return _error(exc)
        return _pdf(pdf, f"Preview {template.name}.pdf")


class ContractSettingsView(APIView):
    def get_permissions(self):
        if self.request.method == "GET":
            return [permissions.IsAuthenticated(), IsStaffMember()]
        return [permissions.IsAuthenticated(), IsStaffMember(), HasConfigsAccess()]

    def get(self, request):
        return Response(ContractSettingsSerializer(ContractSettings.load()).data)

    def patch(self, request):
        serializer = ContractSettingsSerializer(ContractSettings.load(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class ContractViewSet(viewsets.ReadOnlyModelViewSet):
    """A client's contracts (Customers section). Create = from a template
    or an uploaded PDF, optionally emailed at once."""

    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasCustomersAccess]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    pagination_class = None
    filterset_fields = ["customer", "status"]

    def get_queryset(self):
        qs = Contract.objects.select_related("customer", "template", "created_by", "witnessed_by").defer(
            "document_pdf", "signed_pdf", "signature_png", "body_html")
        if self.action == "retrieve":
            qs = qs.prefetch_related("events__user")
        return scope_by_customer(qs, self.request.user, allow_null=False).order_by("-created_at")

    def get_serializer_class(self):
        return ContractDetailSerializer if self.action == "retrieve" else ContractSerializer

    def create(self, request):
        from customers.models import Customer

        data = request.data
        customer = scope_customers_to_user(Customer.objects.all(), request.user).filter(
            pk=data.get("customer") or 0).first()
        if customer is None:
            return _error("Choose the client.")
        kind = data.get("kind") or Contract.Kind.TEMPLATE
        try:
            if kind == Contract.Kind.UPLOAD:
                upload = request.FILES.get("file")
                if upload is None:
                    return _error("Choose a PDF file.")
                if upload.size > services.MAX_UPLOAD_BYTES:
                    return _error("That PDF is larger than 10 MB.")
                contract = services.create_from_upload(customer, upload.read(), upload.name,
                                                       data.get("title", ""), request.user)
            else:
                template = ContractTemplate.objects.filter(pk=data.get("template") or 0).first()
                contract = services.create_from_template(customer, template, request.user)
        except ContractError as exc:
            return _error(exc)
        warning = ""
        if str(data.get("send", "")).lower() in ("1", "true", "yes", "on"):
            try:
                services.send(contract, request.user, data.get("email") or None)
            except ContractError as exc:
                warning = f"Saved, but not sent: {exc}"
        contract.refresh_from_db()
        body = ContractDetailSerializer(contract).data
        body["warning"] = warning
        return Response(body, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        contract = self.get_object()
        try:
            to = services.send(contract, request.user, request.data.get("email") or None)
        except ContractError as exc:
            return _error(exc)
        contract.refresh_from_db()
        return Response({"sent_to": to, "contract": ContractDetailSerializer(contract).data})

    @action(detail=True, methods=["post"], url_path="in-person")
    def in_person(self, request, pk=None):
        contract = self.get_object()
        try:
            url = services.start_in_person(contract, request.user)
        except ContractError as exc:
            return _error(exc)
        return Response({"url": url})

    @action(detail=True, methods=["get"])
    def link(self, request, pk=None):
        contract = self.get_object()
        if not contract.is_open:
            return _error("This contract is already signed or cancelled.")
        return Response({"url": services.signing_url(contract), "expires_at": contract.expires_at,
                         "is_expired": contract.is_expired})

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        contract = self.get_object()
        try:
            services.cancel(contract, request.user, str(request.data.get("reason") or "")[:300])
        except ContractError as exc:
            return _error(exc)
        contract.refresh_from_db()
        return Response(ContractDetailSerializer(contract).data)

    @action(detail=True, methods=["get"])
    def document(self, request, pk=None):
        contract = get_object_or_404(Contract, pk=self.get_object().pk)
        return _pdf(contract.document_pdf, contract.document_filename or f"{contract.reference}.pdf")

    @action(detail=True, methods=["get"])
    def signed(self, request, pk=None):
        contract = get_object_or_404(Contract, pk=self.get_object().pk)
        if not contract.signed_pdf:
            return _error("This contract isn't signed.", status.HTTP_404_NOT_FOUND)
        return _pdf(contract.signed_pdf, services.signed_filename(contract))


# --- the client's side (no login) ------------------------------------------


class SigningThrottle(SimpleRateThrottle):
    scope = "contract_signing"

    def get_rate(self):
        return "60/min"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class PublicContractView(APIView):
    """The signing page's data. The token in the URL is the credential."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [SigningThrottle]

    def contract(self, token):
        return Contract.objects.select_related("customer").filter(token=token).first()

    def get(self, request, token):
        from notifications.email_settings import get_email_config
        from notifications.pdf import _company_block

        contract = self.contract(token)
        if contract is None:
            return _error("This signing link isn't valid.", status.HTTP_404_NOT_FOUND)
        witness = services.witness_from(contract, request.query_params.get("witness"))
        if contract.status == Contract.Status.SIGNED:
            state = "signed"
        elif contract.status == Contract.Status.CANCELLED:
            state = "cancelled"
        elif contract.is_expired and witness is None:
            state = "expired"
        else:
            state = "open"
            if witness is None:
                services.mark_viewed(contract, client_ip(request), request.META.get("HTTP_USER_AGENT", ""))
        company, _ = _company_block()
        customer = contract.customer
        return Response({
            "state": state,
            "reference": contract.reference,
            "title": contract.title,
            "kind": contract.kind,
            "company_name": company["name"] or get_email_config()["company_name"],
            "company_phone": company["phone"],
            "company_email": company["email"],
            "client_name": customer.full_name,
            "client_company": customer.company_name,
            "id_number": customer.id_number,
            "body_html": contract.body_html if state == "open" and contract.kind == Contract.Kind.TEMPLATE else "",
            "pages": services.page_count(contract.document_pdf) if state == "open" else None,
            "expires_at": contract.expires_at,
            "in_person": witness is not None,
            "witness_name": (witness.get_full_name() or witness.username) if witness else "",
            "signed_at": contract.signed_at,
            "signer_name": contract.signer_name if state == "signed" else "",
            "signed_copy": state == "signed" and services.signed_copy_available(contract),
        })


class PublicContractDocumentView(PublicContractView):
    def get(self, request, token):
        contract = self.contract(token)
        if contract is None or contract.status == Contract.Status.CANCELLED:
            return _error("Not found.", status.HTTP_404_NOT_FOUND)
        if contract.status == Contract.Status.SIGNED:
            if not services.signed_copy_available(contract):
                return _error("Not found.", status.HTTP_404_NOT_FOUND)
            return _pdf(contract.signed_pdf, services.signed_filename(contract))
        services.mark_document_opened(contract, client_ip(request), request.META.get("HTTP_USER_AGENT", ""))
        return _pdf(contract.document_pdf, contract.document_filename or f"{contract.reference}.pdf")


class PublicContractSignView(PublicContractView):
    http_method_names = ["post", "options"]

    def post(self, request, token):
        contract = self.contract(token)
        if contract is None:
            return _error("This signing link isn't valid.", status.HTTP_404_NOT_FOUND)
        data = request.data if isinstance(request.data, dict) else {}
        witness = services.witness_from(contract, data.get("witness"))
        try:
            contract = services.sign(
                contract,
                signer_name=data.get("signer_name"),
                signature_data_url=data.get("signature"),
                agreed=data.get("agree") is True,
                id_number=data.get("id_number", ""),
                ip=client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                witness=witness,
            )
        except ContractError as exc:
            return _error(exc)
        return Response({"state": "signed", "signed_at": contract.signed_at, "signer_name": contract.signer_name,
                         "signed_copy": True, "emailed_to": contract.sent_to or contract.customer.email or ""})
