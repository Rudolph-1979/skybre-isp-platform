"""Matching a bank transaction to who it belongs to -- the customer who
paid it in (credit, see match_customer_by_reference) or the supplier it
was paid out to (debit, see match_supplier_by_name below).

Judgment call: matches on the customer's own reference number
(Customer.customer_id, e.g. "CUS-000123") appearing in the transaction's
description -- this is already the reference shown to customers on their
Statement PDF (see notifications/templates/notifications/statement_pdf.html),
so nothing new needs to be communicated to customers; they just need to
keep using it as their EFT/bank-transfer reference, same as before this
feature existed. This was the option chosen over pure amount-matching
(too ambiguous when many customers share a plan/price) when this feature
was scoped.

Deliberately conservative: if a description contains zero or more than one
DISTINCT customer reference, this returns None (no match) rather than
guessing -- an unmatched transaction just sits in the review queue for a
staff member to assign manually, which is always available regardless of
whether reference-matching worked.
"""
import re

# References are compared with everything except letters and digits stripped
# from BOTH sides. A customer whose reference is "CUS-000123" is then matched
# by "CUS 000123", "cus000123" or "Ref:CUS-000123" alike, since all four
# normalise to "CUS000123". Bank narrations mangle spacing and punctuation
# unpredictably, so comparing raw strings matches almost nothing.
_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")


def normalise_reference(value: str) -> str:
    return _NON_ALNUM_RE.sub("", value or "").upper()


# A reference shorter than this is never auto-matched. Same reasoning as
# MIN_SUPPLIER_NAME_LENGTH below: once normalised, a 2-3 character reference
# is a substring of a large share of ordinary bank narrations, so matching on
# it produces confident-looking nonsense.
MIN_REFERENCE_LENGTH = 4

# An all-digit reference has to be longer still. Bank descriptions are full of
# stray numbers -- amounts, dates, card and account fragments -- and because
# normalisation removes the spaces between them, a short numeric reference can
# match ACROSS two unrelated numbers ("...1234 5678..." becomes "12345678",
# which contains "23456"). Letters are far harder to hit by accident; pure
# digits need the extra length to be safe.
MIN_NUMERIC_REFERENCE_LENGTH = 6


def _is_matchable(reference: str) -> bool:
    if len(reference) < MIN_REFERENCE_LENGTH:
        return False
    if reference.isdigit() and len(reference) < MIN_NUMERIC_REFERENCE_LENGTH:
        return False
    return True


def match_customer_by_reference(description: str, customers=None):
    """Returns the matching customers.Customer, or None.

    Matches on the customer's own payment reference (Customer.customer_id)
    appearing in the transaction's description -- the same reference printed
    on their statement, so nothing new has to be communicated to customers;
    they keep using whatever they already use. Chosen over pure amount-
    matching (too ambiguous when many customers share a plan, and therefore a
    price) when this feature was scoped.

    This used to hard-code the auto-generated format with a
    ``CUS[\\s\\-]?(\\d{6})`` regex. That broke the moment references became
    editable: a customer migrated from another system, keeping the reference
    they already put on their EFTs, was invisible to a pattern that only ever
    looked for "CUS-######". Any reference now matches.

    Deliberately conservative, unchanged in spirit from the regex version:

      * No match, or matches against more than one DISTINCT customer, returns
        None rather than guessing.
      * Where one matched reference is contained inside another ("SKY12"
        inside "SKY123"), only the longest counts -- otherwise the shorter one
        would make every such payment permanently ambiguous.
      * Very short references are skipped entirely (see MIN_REFERENCE_LENGTH).

    An unmatched transaction just sits in the review queue to be assigned by
    hand, which is always available regardless of whether this worked.

    `customers` takes a pre-fetched iterable so a batch import doesn't
    re-query per transaction -- same shape as match_supplier_by_name below.
    """
    from customers.models import Customer

    if not description:
        return None

    haystack = normalise_reference(description)
    if not haystack:
        return None

    if customers is None:
        customers = Customer.objects.exclude(customer_id="").only("id", "customer_id")

    hits = []
    for customer in customers:
        reference = normalise_reference(customer.customer_id)
        if not _is_matchable(reference):
            continue
        if reference in haystack:
            hits.append((reference, customer))

    if not hits:
        return None

    # Drop any hit whose reference is a strict substring of another hit's. A
    # description containing "SKY123" also contains "SKY12"; without this,
    # both customers match and the payment is never assigned to either.
    longest = [
        (reference, customer)
        for reference, customer in hits
        if not any(reference != other and reference in other for other, _ in hits)
    ]

    if len({customer.pk for _, customer in longest}) != 1:
        return None
    return longest[0][1]


# A supplier name shorter than this is not matched against descriptions
# at all. Substring matching on a 2-3 character name ("SA", "MTN", or a
# record someone named "IT") hits an enormous share of ordinary bank
# narrations, which would produce confident-looking but meaningless
# suggestions on nearly every debit. Anything shorter simply gets
# assigned by hand.
MIN_SUPPLIER_NAME_LENGTH = 4


def match_supplier_by_name(description: str, suppliers=None):
    """Returns the matching inventory.Supplier, or None.

    Unlike customer matching, there's no dedicated reference code to look
    for -- Supplier has no equivalent of Customer.customer_id (see
    inventory.models.Supplier) -- so this instead checks whether exactly
    one existing Supplier's name appears (case-insensitive) in the
    transaction's description, which is usually how a bank statement
    line for something like "TELKOM SA" or "VODACOM" reads. Same
    deliberately-conservative rule as customer matching: zero or more
    than one distinct Supplier name found in the description means no
    match, not a guess -- the transaction just sits in the review queue
    for a staff member to assign manually (or type a free-text supplier
    name directly when confirming), which always works regardless of
    whether this heuristic succeeds. Since every match still requires an
    explicit human Confirm before it becomes a real Expense, a wrong
    suggestion here can never silently post -- it can only be corrected
    or overridden first.

    `suppliers` lets a caller pass an already-fetched list so a bulk
    import doesn't re-read the whole Supplier table once per row (see
    BankTransactionViewSet.import_commit, which fetches it once).
    """
    if not description:
        return None

    if suppliers is None:
        from inventory.models import Supplier

        suppliers = list(Supplier.objects.all().only("id", "name"))

    description_lower = description.lower()
    matches = [
        s for s in suppliers
        if s.name and len(s.name.strip()) >= MIN_SUPPLIER_NAME_LENGTH
        and s.name.strip().lower() in description_lower
    ]
    if len(matches) != 1:
        return None
    return matches[0]
