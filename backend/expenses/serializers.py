from django.conf import settings
from rest_framework import serializers

from config.media_security import sign_media_path
from .models import Expense


class ExpenseSerializer(serializers.ModelSerializer):
    """Attachment is turned into a signed, short-lived download link on
    read (same convention as inventory.StockReceipt's supplier-invoice
    attachments -- see config/media_security.py) rather than a bare,
    unauthenticated /media/ URL, since expense receipts are internal
    financial records."""

    vat_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    amount_incl_vat = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    supplier_display_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    from_bank_feed = serializers.SerializerMethodField()

    class Meta:
        model = Expense
        fields = [
            "id", "supplier", "supplier_name", "supplier_display_name", "category", "description",
            "invoice_number", "date", "amount_excl_vat", "vat_rate_pct", "vat_amount", "amount_incl_vat",
            "attachment", "notes", "created_by", "created_by_name", "from_bank_feed", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_by", "created_at", "updated_at"]
        extra_kwargs = {"attachment": {"write_only": True, "required": False}}

    def get_supplier_display_name(self, obj):
        if obj.supplier_id:
            return obj.supplier.name
        return obj.supplier_name or "—"

    def get_created_by_name(self, obj):
        if not obj.created_by:
            return ""
        return f"{obj.created_by.first_name} {obj.created_by.last_name}".strip() or obj.created_by.username

    def to_internal_value(self, data):
        # An empty `supplier` means "not linked to a Supplier record" --
        # the explicit blank option in the Expenses form. Without this,
        # DRF rejects "" as an invalid pk, so the frontend had to omit the
        # key entirely to avoid an error, which meant an existing link
        # could never actually be cleared: the FK survived while a
        # contradictory free-text supplier_name was saved alongside it.
        # Normalising ""/null to None makes un-linking work.
        if hasattr(data, "get") and data.get("supplier", "__absent__") in ("", None):
            data = data.copy()
            data["supplier"] = None
        return super().to_internal_value(data)

    def validate(self, attrs):
        # A supplier is either a real Supplier record or a free-text name,
        # never both -- otherwise supplier_display_name silently prefers
        # the FK and the typed name becomes invisible, contradictory data.
        supplier = attrs.get("supplier", getattr(self.instance, "supplier", None))
        if supplier is not None:
            attrs["supplier_name"] = ""

        # Wages carry no VAT. There is no tax invoice behind a salary and
        # no input tax to claim, but vat_rate_pct defaults to 15 for every
        # category, so capturing a payroll run on the Expenses tab and
        # leaving the rate alone claimed 15% input VAT on the whole wage
        # bill -- easily the largest single cost here -- and understated
        # the VAT payable on the return by that much.
        #
        # The bank-feed side of this already refuses to guess a rate at
        # all, and its message names this exact case: "use 0 for a
        # purchase that carries no VAT (salaries, transfers, loan
        # repayments)". This is the manual side agreeing with it.
        # Only judge what this request actually writes. Enforcing it on every
        # save meant an expense captured at 15% before this rule existed
        # could not be edited AT ALL -- correcting its description came back
        # 400 about a VAT rate the request never mentioned.
        touches_vat = "category" in attrs or "vat_rate_pct" in attrs
        if not touches_vat and self.instance is not None:
            return attrs

        category = attrs.get("category", getattr(self.instance, "category", None))
        # The rate has a MODEL DEFAULT of 15, so an omitted key on create is
        # not "no rate" -- it is 15, which is the single most likely way to
        # book a payroll run: fill the form in, never touch the VAT field.
        # Reading attrs alone missed exactly that case, which is the case
        # this guard exists for.
        if "vat_rate_pct" in attrs:
            rate = attrs["vat_rate_pct"]
        elif self.instance is not None:
            rate = self.instance.vat_rate_pct
        else:
            rate = Expense._meta.get_field("vat_rate_pct").get_default()
        if category == Expense.Category.SALARIES and rate:
            raise serializers.ValidationError({
                "vat_rate_pct": (
                    "Salaries carry no VAT, so no input VAT can be claimed on them -- set the "
                    "rate to 0. If this is a payroll bureau's own invoice, that is a "
                    "professional fee and does carry VAT: file it under Professional fees."
                )
            })
        return attrs

    def get_from_bank_feed(self, obj):
        # True once this Expense was created via BankTransactionViewSet's
        # confirm() action on a debit transaction (see bankfeeds/models.py's
        # created_expense) -- lets the Expenses tab show a small audit-trail
        # badge for expenses that came from the bank feed rather than
        # manual entry.
        return hasattr(obj, "bank_transaction")

    def to_representation(self, instance):
        # Same signed-link approach as inventory's supplier-invoice
        # attachments/payroll's sick-note attachments -- an expense
        # receipt is internal financial data, not reachable via a bare,
        # unauthenticated /media/ URL.
        data = super().to_representation(instance)
        if instance.attachment:
            relative_path = instance.attachment.name
            signed = sign_media_path(relative_path)
            url = f"{settings.MEDIA_URL}{relative_path}?sig={signed}"
            request = self.context.get("request")
            data["attachment"] = request.build_absolute_uri(url) if request else url
        else:
            data["attachment"] = None
        return data
