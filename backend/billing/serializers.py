from rest_framework import serializers
from .models import Tariff, Service, Invoice, InvoiceItem, Payment


class TariffSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tariff
        fields = "__all__"
        read_only_fields = ["id", "created_at"]


class ServiceSerializer(serializers.ModelSerializer):
    tariff_name = serializers.CharField(source="tariff.name", read_only=True)
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    price = serializers.DecimalField(source="tariff.price", max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Service
        fields = [
            "id", "customer", "customer_name", "tariff", "tariff_name", "price",
            "status", "device", "start_date", "end_date", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class InvoiceItemSerializer(serializers.ModelSerializer):
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = InvoiceItem
        fields = ["id", "invoice", "service", "description", "quantity", "unit_price", "tax_rate_pct", "total"]
        read_only_fields = ["id"]


class InvoiceSerializer(serializers.ModelSerializer):
    items = InvoiceItemSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    balance_due = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            "id", "number", "customer", "customer_name", "status", "date_created", "date_due",
            "subtotal", "tax_total", "total", "paid_amount", "balance_due", "note", "items",
        ]
        read_only_fields = ["id", "number", "date_created", "subtotal", "tax_total", "total", "paid_amount"]

    def get_balance_due(self, obj):
        return obj.total - obj.paid_amount


class InvoiceCreateSerializer(serializers.ModelSerializer):
    """Accepts nested items on creation and computes totals."""

    items = InvoiceItemSerializer(many=True)

    class Meta:
        model = Invoice
        fields = ["id", "customer", "date_due", "note", "items", "status"]

    def create(self, validated_data):
        items_data = validated_data.pop("items")
        invoice = Invoice.objects.create(**validated_data)
        for item in items_data:
            InvoiceItem.objects.create(invoice=invoice, **item)
        invoice.recalc_totals()
        return invoice


class PaymentSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    received_by_name = serializers.CharField(source="received_by.username", read_only=True, default=None)

    class Meta:
        model = Payment
        fields = [
            "id", "customer", "customer_name", "invoice", "amount", "method",
            "date", "note", "received_by", "received_by_name",
        ]
        read_only_fields = ["id", "date"]

    def create(self, validated_data):
        validated_data["received_by"] = self.context["request"].user
        payment = super().create(validated_data)
        if payment.invoice:
            payment.invoice.paid_amount += payment.amount
            if payment.invoice.paid_amount >= payment.invoice.total:
                payment.invoice.status = Invoice.Status.PAID
            payment.invoice.save()
        customer = payment.customer
        customer.balance = customer.balance - payment.amount
        customer.save()
        return payment
