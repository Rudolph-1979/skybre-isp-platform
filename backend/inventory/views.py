from rest_framework import viewsets, permissions, mixins
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.permissions import IsStaffMember, section_permission
from .models import Supplier, Product, SerializedUnit, StockReceipt, StockIssue, StockMovement

HasInventoryAccess = section_permission("inventory")
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
    ordering_fields = ["name", "created_at"]
    search_fields = ["name", "contact_person", "email", "phone"]

    def get_permissions(self):
        # Listing suppliers is open to any staff member (not just the
        # Inventory section) because other sections need a supplier picker
        # of their own -- Accountant -> Expenses and Accountant -> Bank
        # Feeds both let staff attribute a purchase/debit to a supplier,
        # and gating the list on Inventory left those dropdowns silently
        # empty for accountant-only staff. Exactly the same reasoning (and
        # shape) as CustomerViewSet.get_permissions, which opens "list" up
        # so Finance/Services/Scheduling/Tickets can render a customer
        # picker. Creating/editing/deleting a supplier record still
        # requires the Inventory section itself.
        if self.action in ("list", "retrieve"):
            return [permissions.IsAuthenticated(), IsStaffMember()]
        return [permissions.IsAuthenticated(), IsStaffMember(), HasInventoryAccess()]

    def get_queryset(self):
        return Supplier.objects.all()


class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    filterset_class = ProductFilter
    ordering_fields = ["name", "category", "tracking_type", "created_at"]
    search_fields = ["name", "sku", "description"]

    def get_permissions(self):
        # list/retrieve stay open to any staff member (regardless of
        # Inventory section access) since Finance's invoice/quote line
        # items and Services both need to browse the product catalog to
        # pick stock items -- only actually changing stock (create/
        # update/delete/adjust) requires the Inventory section itself.
        if self.action in ("list", "retrieve"):
            return [permissions.IsAuthenticated(), IsStaffMember()]
        return [permissions.IsAuthenticated(), IsStaffMember(), HasInventoryAccess()]

    def get_queryset(self):
        # The cost/margin properties walk this product's receipt lines and each
        # line reads its own receipt (to know whether the captured cost was
        # VAT-inclusive). Prefetched here so listing the catalogue is a fixed
        # number of queries rather than growing with the number of products.
        return Product.objects.prefetch_related("receipt_lines__receipt")

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


class SerializedUnitViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Browse and correct individual serial/MAC-tracked units — lookups,
    warranty checks, and seeing what's issued where.

    List + retrieve + update, deliberately no create and no delete. Units are
    brought into existence by checking in a receipt (which is what ties them
    to a supplier and a cost) and destroying one would orphan the movement
    history that the on-hand count is derived from. What update is for is
    fixing a typo: a mis-keyed MAC used to be permanent, which made recording
    MACs at all a bit pointless. The serializer restricts editing to
    serial_number / mac_address / notes and validates both identifiers,
    including against every other unit.
    """

    serializer_class = SerializedUnitSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasInventoryAccess]
    filterset_class = SerializedUnitFilter
    ordering_fields = ["serial_number", "mac_address", "status", "created_at"]
    search_fields = ["serial_number", "mac_address", "product__name"]

    def get_queryset(self):
        # The provenance fields on the serializer walk
        # received_via_line -> receipt -> supplier. Without this the units
        # list is three extra queries per row.
        return SerializedUnit.objects.select_related(
            "product", "received_via_line__receipt__supplier"
        ).all()


class StockReceiptViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasInventoryAccess]
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
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasInventoryAccess]
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
