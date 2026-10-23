"""Finding the client a job is for from its title (7 Oct 2026).

Jobs are often booked with the client typed into the title --
"Thami Guzana SR2547 KPS Rental install" -- and the Client field left
empty. A job with no client sends the technician no name or phone number
(Conecto's scheduleJob) and, with no location typed, no address either. When
the title carries a client code that exists, the job is linked to that client.
"""
import re

#: SWF2827, SR2547, SMF047, SCCTV005, CUS-001598 ...
CODE_RE = re.compile(r"\b(CUS-\d{4,6}|S[A-Z]{1,4}\d{3,4})\b", re.IGNORECASE)


def client_from_title(title):
    from customers.models import Customer

    for code in CODE_RE.findall(title or ""):
        customer = Customer.objects.filter(customer_id__iexact=code).first()
        if customer is not None:
            return customer
    return None
