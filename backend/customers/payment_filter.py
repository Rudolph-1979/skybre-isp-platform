"""?payment_status= on the Customers list (the Payment status filter).

The values match the Payment status column (customers.payment_status);
"owing" is everything with something outstanding, and "overdue" takes in
the partially-paid-and-overdue ones too, the way staff ask the question.
"""
from .payment_status import annotate_payment_status

#: filter value -> the payment_rank values it matches.
FILTER_RANKS = {
    "owing": (0, 1, 2, 3),
    "overdue": (0, 1),
    "overdue_partial": (0,),
    "partial": (2,),
    "unpaid": (3,),
    "paid": (4,),
    "credit": (5,),
}


def filter_payment_status(queryset, value):
    ranks = FILTER_RANKS.get((value or "").strip().lower())
    if ranks is None:
        return queryset
    if "payment_rank" not in queryset.query.annotations:
        queryset = annotate_payment_status(queryset)
    return queryset.filter(payment_rank__in=ranks)
