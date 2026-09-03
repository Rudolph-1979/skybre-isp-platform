from rest_framework import viewsets, permissions, serializers
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
            # assigned_to and status are staff decisions. A customer could
            # otherwise pick which staff member their ticket landed on and
            # what state it opened in.
            serializer.save(customer=customer_profile, assigned_to=None, status=Ticket.Status.OPEN)
        else:
            serializer.save()

    def perform_update(self, serializer):
        user = self.request.user
        if not user.is_staff_member:
            # `customer` is writable for staff (re-filing a ticket against
            # the right account is a real support action), and get_queryset
            # already stops a customer reaching anybody else's ticket. But
            # nothing stopped them PATCHing their OWN ticket onto another
            # customer, which moved the ticket and its whole comment thread
            # off their account and onto a stranger's.
            serializer.save(
                customer=serializer.instance.customer,
                assigned_to=serializer.instance.assigned_to,
            )
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
            # Reading the whole non-internal thread is the point of the
            # portal, but writing to it is not: for anything that mutates,
            # a customer is scoped to comments they wrote themselves.
            #
            # Without this, the read scope doubled as the write scope, so a
            # customer could PATCH the support agent's reply on their own
            # ticket -- the edited text still rendering under the agent's
            # name -- or DELETE it outright, erasing what support actually
            # told them.
            if self.action not in ("list", "retrieve"):
                qs = qs.filter(author=user)
        return qs

    def _customer_ticket_or_400(self, user, ticket):
        """A customer may only attach a comment to one of their own tickets.

        perform_create stamped `author` and forced is_internal=False, but
        never checked the `ticket` FK -- and ticket ids are sequential, so
        a customer could post into any other customer's thread by guessing
        one. The comment then appeared in that customer's portal, which is
        an impersonation channel (bank details "from accounts") rather than
        just a data-integrity problem.
        """
        customer_profile = getattr(user, "customer_profile", None)
        if customer_profile is None or ticket is None or ticket.customer_id != customer_profile.pk:
            raise serializers.ValidationError({"ticket": "That ticket isn't yours."})

    def perform_create(self, serializer):
        user = self.request.user
        is_internal = serializer.validated_data.get("is_internal", False)
        if not user.is_staff_member:
            is_internal = False
            self._customer_ticket_or_400(user, serializer.validated_data.get("ticket"))
        serializer.save(author=user, is_internal=is_internal)

    def perform_update(self, serializer):
        user = self.request.user
        if not user.is_staff_member:
            # perform_create forced is_internal=False; update did not, so a
            # customer could flip their own comment to internal and hide it
            # from the portal, or move it onto another ticket.
            self._customer_ticket_or_400(user, serializer.validated_data.get("ticket", serializer.instance.ticket))
            serializer.save(is_internal=False, ticket=serializer.instance.ticket)
        else:
            serializer.save()
