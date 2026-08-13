from rest_framework import viewsets, permissions
from .models import Customer
from .serializers import CustomerSerializer
from accounts.permissions import IsStaffMember


class CustomerViewSet(viewsets.ModelViewSet):
    serializer_class = CustomerSerializer
    filterset_fields = ["status", "category", "customer_type"]
    search_fields = ["full_name", "company_name", "email", "phone", "customer_id"]
    ordering_fields = ["created_at", "full_name", "balance"]

    def get_permissions(self):
        if self.action == "retrieve":
            return [permissions.IsAuthenticated()]
        return [permissions.IsAuthenticated(), IsStaffMember()]

    def get_queryset(self):
        user = self.request.user
        qs = Customer.objects.select_related("assigned_staff").all()
        if user.is_staff_member:
            return qs
        customer_profile = getattr(user, "customer_profile", None)
        if customer_profile is None:
            return qs.none()
        return qs.filter(pk=customer_profile.pk)
