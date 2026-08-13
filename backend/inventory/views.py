from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.permissions import IsStaffMember
from .models import Supplier, Product, SerializedUnit, StockReceipt, StockIssue, StockMovement
from .serializers import (
    SupplierSerializer,
    ProductSerializer,
    SerializedUnitSerializer,
    StockReceiptSerializer,
    StockReceiptCreateSerializer,
    StockIssueSerializer,
    StockIssueCreateSerializer,
)
from .filters import ProductFilter, SerializedUnitFilter, StockReceiptFilter, StockIssueFilter


class SupplierViewSet(viewsets.ModelViewSet):
    serializer_class = SupplierSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember]
    ordering_fields = ["name", "created_at"]
    search_fields = ["name", "contact_person", "email", "phone"]

    def get_queryset(self):
        return Supplier.objects.all()


class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember]
    filterset_class = ProductFilter
    ordering_fields = ["name", "category", "tracking_type", "created_at"]
    search_fields = ["name", "sku", "description"]

    def get_queryset(self):
        return Product.objects.all()

    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None):
        """Manual correction for quantity-tracked products (damaged,
        lost, recount). Serialized units are corrected individually via
        their own status, not through this endpoint."""
        product = self.get_object()
        if product.tracking_type != Product.TrackingType.QUANTITY:
            return Response(
                {
                    "detail": "Manual adjustments are only for quantity-tracked products. "
                    "Update the specific serialized unit's status instead."
                },
                status=400,
            )
        try:
            delta = int(request.data.get("quantity"))
        except (TypeError, ValueError):
            return Response(
                {"detail": "quantity must be a non-zero integer (positive to add, negative to remove)."},
                status=400,
            )
        if delta == 0:
            return Response({"detail": "quantity must be non-zero."}, status=400)
        if delta < 0 and product.quantity_on_hand + delta < 0:
            return Response(
                {"detail": f"Cannot remove {-delta} — only {product.quantity_on_hand} in stock."},
                status=400,
            )
        note = request.data.get("note", "") or "Manual adjustment"
        StockMovement.objects.create(
            product=product,
            movement_type=StockMovement.MovementType.ADJUSTMENT,
            quantity=delta,
            note=note,
            created_by=request.user,
        )
        return Response(ProductSerializer(product).data)


class SerializedUnitViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only browse of individual serial/MAC-tracked units — for
    lookups, warranty checks, and seeing what's issued where. Status
    changes happen via the receipt/issue flows."""

    serializer_class = SerializedUnitSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember]
    filterset_class = SerializedUnitFilter
    ordering_fields = ["serial_number", "status", "created_at"]
    search_fields = ["serial_number", "mac_address", "product__name"]

    def get_queryset(self):
        return SerializedUnit.objects.select_related("product").all()


class StockReceiptViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, IsStaffMember]
    filterset_class = StockReceiptFilter
    ordering_fields = ["invoice_date", "created_at", "supplier__name"]
    search_fields = ["invoice_number", "supplier__name", "notes"]

    def get_serializer_class(self):
        if self.action == "create":
            return StockReceiptCreateSerializer
        return StockReceiptSerializer

    def get_queryset(self):
        return (
            StockReceipt.objects.select_related("supplier", "received_by")
            .prefetch_related("lines__units", "lines__product")
            .all()
        )


class StockIssueViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, IsStaffMember]
    filterset_class = StockIssueFilter
    ordering_fields = ["issued_at", "job__title", "issued_to__username"]
    search_fields = ["notes", "job__title", "issued_to__username"]

    def get_serializer_class(self):
        if self.action == "create":
            return StockIssueCreateSerializer
        return StockIssueSerializer

    def get_queryset(self):
        return (
            StockIssue.objects.select_related("job", "issued_to", "job__customer")
            .prefetch_related("lines__product", "lines__serial_unit")
            .all()
        )
