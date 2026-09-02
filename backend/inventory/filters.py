import django_filters
from .models import Product, SerializedUnit, StockReceipt, StockIssue


class ProductFilter(django_filters.FilterSet):
    class Meta:
        model = Product
        fields = ["category", "tracking_type", "is_active"]


class SerializedUnitFilter(django_filters.FilterSet):
    # Which supplier a unit came from isn't a field on the unit -- it's two
    # hops away, through the receipt line it arrived on. Exposed as a filter
    # because "what did we buy from this supplier" is the question the Units
    # list exists to answer.
    supplier = django_filters.NumberFilter(field_name="received_via_line__receipt__supplier")
    category = django_filters.CharFilter(field_name="product__category")

    class Meta:
        model = SerializedUnit
        fields = ["product", "status", "supplier", "category"]


class StockReceiptFilter(django_filters.FilterSet):
    date_from = django_filters.DateFilter(field_name="invoice_date", lookup_expr="gte")
    date_to = django_filters.DateFilter(field_name="invoice_date", lookup_expr="lte")

    class Meta:
        model = StockReceipt
        fields = ["supplier", "date_from", "date_to"]


class StockIssueFilter(django_filters.FilterSet):
    class Meta:
        model = StockIssue
        fields = ["job", "issued_to"]
