from rest_framework import permissions, viewsets
from rest_framework.response import Response

from accounts.permissions import IsStaffMember, section_permission
from .models import Account
from .serializers import AccountSerializer

HasAccountantAccess = section_permission("accountant")


class AccountViewSet(viewsets.ModelViewSet):
    """The chart of accounts.

    Gated on the accountant section, like everything else that describes
    the shape of the business's money.
    """

    serializer_class = AccountSerializer
    queryset = Account.objects.all()
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasAccountantAccess]
    filterset_fields = ["type", "is_active"]
    search_fields = ["code", "name"]
    ordering_fields = ["code", "name", "type"]

    def destroy(self, request, *args, **kwargs):
        """Refuse to delete an account that is either the platform's own
        or has anything filed against it -- and say which.

        Deleting an account with expenses on it would either take those
        expenses with it or detach them, and a detached expense is a hole
        in a VAT period that nothing on screen would report. Deactivating
        is the right move and the message says so.
        """
        account = self.get_object()
        if account.is_system:
            return Response(
                {"detail": (
                    f"{account.code} is one of the accounts this platform relies on, so it can't be "
                    "deleted. You can rename or renumber it to match your own chart, or switch it "
                    "off to hide it from the pickers."
                )},
                status=400,
            )
        used = account.expenses.count()
        if used:
            return Response(
                {"detail": (
                    f"{used} expense(s) are filed against {account.code}. Deleting it would leave "
                    "them with no account, which would quietly change your figures. Switch it off "
                    "instead -- it stays on those expenses and disappears from the pickers."
                )},
                status=400,
            )
        return super().destroy(request, *args, **kwargs)
