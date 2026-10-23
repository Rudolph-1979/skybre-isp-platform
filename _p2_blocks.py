SETTINGS_ANCHOR = '''    "conecto",
]'''

SETTINGS_REPLACEMENT = '''    "conecto",
    # The chart of accounts. Its own app rather than a corner of expenses:
    # invoices, bank accounts and VAT all describe themselves in terms of
    # accounts too, and burying the chart inside one consumer makes that
    # one special.
    "ledger",
]'''

URLS_ANCHOR = '''    path("api/", include("sales.urls")),'''

URLS_REPLACEMENT = '''    path("api/", include("sales.urls")),
    path("api/", include("ledger.urls")),'''

SUPPLIER_ANCHOR = '''class Supplier(models.Model):
    """A vendor Skybre buys network/CPE equipment and consumables from."""

    name = models.CharField(max_length=255, unique=True)'''

SUPPLIER_REPLACEMENT = '''class SupplierCategory(models.Model):
    """A grouping of suppliers that share a default expense account.

    The point is the account, not the label. Capturing an invoice from
    Teraco should already know it is bandwidth; typing that on every
    invoice is how a month of expenses ends up half-categorised.

    It is only ever a DEFAULT. The account that counts is the one stored
    on the expense (expenses.Expense.account), for the same reason the
    VAT rate that counts lives on the receipt line rather than on the
    supplier: a supplier's category can change, and that must not
    retroactively re-post invoices already captured and already declared
    in a VAT period.
    """

    name = models.CharField(max_length=100, unique=True)
    default_account = models.ForeignKey(
        "ledger.Account",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supplier_categories",
        limit_choices_to={"type": "expense", "is_active": True},
        help_text="Pre-fills the account when capturing an expense from a supplier in this category.",
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "supplier categories"

    def __str__(self):
        return self.name


class Supplier(models.Model):
    """A vendor Skybre buys network/CPE equipment and consumables from."""

    name = models.CharField(max_length=255, unique=True)
    # SET_NULL, not CASCADE: deleting a category is an act of tidying the
    # list, and it must not take suppliers with it.
    category = models.ForeignKey(
        SupplierCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="suppliers",
    )'''

INV_SERIALIZER_ANCHOR = '''class SupplierSerializer(serializers.ModelSerializer):'''

INV_SERIALIZER_REPLACEMENT = '''class SupplierCategorySerializer(serializers.ModelSerializer):
    default_account_code = serializers.CharField(
        source="default_account.code", read_only=True, default=None
    )
    default_account_name = serializers.CharField(
        source="default_account.name", read_only=True, default=None
    )
    supplier_count = serializers.SerializerMethodField()

    class Meta:
        model = SupplierCategory
        fields = [
            "id", "name", "default_account", "default_account_code", "default_account_name",
            "notes", "is_active", "supplier_count", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_supplier_count(self, obj):
        return obj.suppliers.count()


class SupplierSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)
    # The account an expense from this supplier will pre-fill with, so the
    # Expenses form can show it without fetching the category list and
    # working it out for itself.
    default_account = serializers.IntegerField(
        source="category.default_account_id", read_only=True, default=None
    )
    default_account_name = serializers.CharField(
        source="category.default_account.name", read_only=True, default=None
    )'''

INV_VIEWS_ANCHOR = '''class SupplierViewSet(viewsets.ModelViewSet):'''

INV_VIEWS_REPLACEMENT = '''class SupplierCategoryViewSet(viewsets.ModelViewSet):
    """Supplier categories and the expense account each one pre-fills.

    Same permission shape as SupplierViewSet: anybody on staff can read
    the list, because the Expenses form needs it to explain where a
    pre-filled account came from, but changing one needs the Inventory
    section -- that is where suppliers are maintained.
    """

    serializer_class = SupplierCategorySerializer
    queryset = SupplierCategory.objects.select_related("default_account").all()
    ordering_fields = ["name"]
    search_fields = ["name"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [permissions.IsAuthenticated(), IsStaffMember()]
        return [permissions.IsAuthenticated(), IsStaffMember(), HasInventoryAccess()]

    def destroy(self, request, *args, **kwargs):
        """Deleting a category detaches its suppliers rather than removing
        them -- but say how many first, because "delete Bandwidth" reads
        like it affects a label and actually un-categorises every supplier
        under it."""
        category = self.get_object()
        count = category.suppliers.count()
        if count and request.query_params.get("confirm") != "yes":
            return Response(
                {"detail": (
                    f"{count} supplier(s) are in {category.name}. Deleting it leaves them with no "
                    "category, and expenses already captured keep the account they were saved with. "
                    "Re-send with ?confirm=yes to go ahead."
                )},
                status=400,
            )
        return super().destroy(request, *args, **kwargs)


class SupplierViewSet(viewsets.ModelViewSet):'''

EXPENSE_MODEL_ANCHOR = '''    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)'''

EXPENSE_MODEL_REPLACEMENT = '''    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    # The chart account this expense belongs to.
    #
    # Kept ALONGSIDE `category` rather than replacing it. category still
    # drives the salaries/VAT rule and the existing reporting, and swapping
    # one for the other in a single release would mean rewriting both in a
    # migration that also has to be right about VAT. The account is the
    # richer one and can grow into the primary; for now it is stamped from
    # the supplier's category on capture, and stored, so that changing a
    # supplier's category later never re-posts an expense already declared
    # in a filed VAT period.
    account = models.ForeignKey(
        "ledger.Account",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
        limit_choices_to={"type": "expense"},
    )'''

EXPENSE_SER_FIELDS_ANCHOR = '''            "id", "supplier", "supplier_name", "supplier_display_name", "category", "description",'''

EXPENSE_SER_FIELDS_REPLACEMENT = '''            "id", "supplier", "supplier_name", "supplier_display_name", "category",
            "account", "account_code", "account_name", "description",'''

EXPENSE_SER_DECL_ANCHOR = '''    supplier_display_name = serializers.SerializerMethodField()'''

EXPENSE_SER_DECL_REPLACEMENT = '''    supplier_display_name = serializers.SerializerMethodField()
    account_code = serializers.CharField(source="account.code", read_only=True, default=None)
    account_name = serializers.CharField(source="account.name", read_only=True, default=None)'''

EXPENSE_SER_VALIDATE_ANCHOR = '''    def validate(self, attrs):
        # A supplier is either a real Supplier record or a free-text name,
        # never both -- otherwise supplier_display_name silently prefers
        # the FK and the typed name becomes invisible, contradictory data.
        supplier = attrs.get("supplier", getattr(self.instance, "supplier", None))
        if supplier is not None:
            attrs["supplier_name"] = ""'''

EXPENSE_SER_VALIDATE_REPLACEMENT = '''    def validate(self, attrs):
        # A supplier is either a real Supplier record or a free-text name,
        # never both -- otherwise supplier_display_name silently prefers
        # the FK and the typed name becomes invisible, contradictory data.
        supplier = attrs.get("supplier", getattr(self.instance, "supplier", None))
        if supplier is not None:
            attrs["supplier_name"] = ""

        # Stamp the account from the supplier's category, but only when
        # this request did not name one and there isn't one already.
        #
        # Server-side, so the figure does not depend on the form having
        # got it right, and ONLY on the way in -- re-deriving it on every
        # save would mean re-categorising a supplier silently moved every
        # expense ever captured from them, including ones inside VAT
        # periods that have already been filed.
        if "account" not in attrs and getattr(self.instance, "account_id", None) is None:
            category = getattr(supplier, "category", None)
            default_account = getattr(category, "default_account", None)
            if default_account is not None:
                attrs["account"] = default_account'''
