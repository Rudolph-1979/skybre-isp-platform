import django_filters
from .models import Product, SerializedUnit, StockReceipt, StockIssue


class ProductFilter(django_filters.FilterSet):
    class Meta:
        model = Product
        fields = ["category", "tracking_type", "is_active"]


class SerializedUnitFilter(django_filters.FilterSet):
    class Meta:
        model = SerializedUnit
        fields = ["product", "status"]


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
