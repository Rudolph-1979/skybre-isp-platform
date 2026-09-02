from rest_framework import viewsets, permissions
from .models import Ticket, TicketComment
from .serializers import TicketSerializer, TicketCommentSerializer
from accounts.permissions import section_permission

HasTicketsAccess = section_permission("tickets")


class TicketViewSet(viewsets.ModelViewSet):
    serializer_class = TicketSerializer
    # Shared with the customer portal -- HasTicketsAccess passes straight
    # through for customers (section restrictions are a staff-only
    # concept, see accounts.permissions.user_can_access_section), so this
    # only narrows things for staff who lack the Support Tickets section.
    permission_classes = [permissions.IsAuthenticated, HasTicketsAccess]
    filterset_fields = ["status", "priority", "department", "customer"]
    search_fields = ["subject", "ticket_number"]

    def get_queryset(self):
        user = self.request.user
        qs = Ticket.objects.select_related("customer", "assigned_to").all()
        if user.is_staff_member:
            return qs
        customer_profile = getattr(user, "customer_profile", None)
        if customer_profile is None:
            return qs.none()
        return qs.filter(customer=customer_profile)

    def perform_create(self, serializer):
        user = self.request.user
        if not user.is_staff_member:
            customer_profile = getattr(user, "customer_profile", None)
            serializer.save(customer=customer_profile)
        else:
            serializer.save()


class TicketCommentViewSet(viewsets.ModelViewSet):
    serializer_class = TicketCommentSerializer
    permission_classes = [permissions.IsAuthenticated, HasTicketsAccess]
    filterset_fields = ["ticket"]

    def get_queryset(self):
        user = self.request.user
        qs = TicketComment.objects.select_related("ticket", "author").all()
        if not user.is_staff_member:
            qs = qs.filter(is_internal=False, ticket__customer__user=user)
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        is_internal = serializer.validated_data.get("is_internal", False)
        if not user.is_staff_member:
            is_internal = False
        serializer.save(author=user, is_internal=is_internal)
