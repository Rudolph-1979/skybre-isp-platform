"""Translating between Skybre's vocabulary and Conecto's.

Skybre has four ticket statuses and four priorities; Conecto has three
and two. Every mapping here is therefore LOSSY IN ONE DIRECTION, and the
losses are chosen deliberately rather than falling out of whichever
`if` was written first:

  * RESOLVED and CLOSED both become "closed" outbound. Conecto has no
    resolved, and the subscriber-facing meaning of both is "we're done".
  * Inbound, "closed" becomes CLOSED rather than RESOLVED. A close that
    came from outside is not a claim that anything was fixed.
  * PENDING (waiting on the customer) becomes "in_progress", because
    from the subscriber's side the ticket is still live.
  * LOW and MEDIUM become "normal"; HIGH and URGENT become "high".
    Inbound, "normal" becomes MEDIUM -- the platform's own default --
    rather than LOW, so an incoming ticket is not quietly deprioritised
    below what a customer phoning in would get.

The round trip is therefore NOT the identity: a RESOLVED ticket that
goes out and comes back is CLOSED. That is accepted; the alternative is
inventing a resolved state in Conecto that its app does not display.
"""
import logging

logger = logging.getLogger(__name__)

from tickets.models import Ticket

# --- status ---------------------------------------------------------------

STATUS_TO_CONECTO = {
    Ticket.Status.OPEN: "open",
    Ticket.Status.PENDING: "in_progress",
    Ticket.Status.RESOLVED: "closed",
    Ticket.Status.CLOSED: "closed",
}

STATUS_FROM_CONECTO = {
    "open": Ticket.Status.OPEN,
    "in_progress": Ticket.Status.PENDING,
    "closed": Ticket.Status.CLOSED,
}

# --- priority -------------------------------------------------------------

PRIORITY_TO_CONECTO = {
    Ticket.Priority.LOW: "normal",
    Ticket.Priority.MEDIUM: "normal",
    Ticket.Priority.HIGH: "high",
    Ticket.Priority.URGENT: "high",
}

PRIORITY_FROM_CONECTO = {
    "normal": Ticket.Priority.MEDIUM,
    "high": Ticket.Priority.HIGH,
}

# --- category / department ------------------------------------------------
#
# Conecto's categories are about what the subscriber is complaining about;
# Skybre's departments are about who deals with it. Only billing lines up
# cleanly. Everything technical lands on Support, which is where it would
# be routed by hand anyway.

CATEGORY_FROM_CONECTO = {
    "billing": Ticket.Department.BILLING,
    "no_internet": Ticket.Department.SUPPORT,
    "slow_speeds": Ticket.Department.SUPPORT,
    "hardware": Ticket.Department.SUPPORT,
    "other": Ticket.Department.SUPPORT,
    "general": Ticket.Department.SUPPORT,
}

DEPARTMENT_TO_CATEGORY = {
    Ticket.Department.BILLING: "billing",
    Ticket.Department.SUPPORT: "other",
    Ticket.Department.SALES: "other",
    Ticket.Department.ABUSE: "other",
}


def status_to_conecto(status):
    return STATUS_TO_CONECTO.get(status, "open")


def status_from_conecto(value):
    """Unknown values return None so the caller can leave the ticket alone.

    Defaulting an unrecognised status to OPEN would silently REOPEN a
    closed ticket on a typo, which is worse than ignoring the field.
    """
    return STATUS_FROM_CONECTO.get((value or "").strip().lower())


def priority_to_conecto(priority):
    return PRIORITY_TO_CONECTO.get(priority, "normal")


def priority_from_conecto(value):
    return PRIORITY_FROM_CONECTO.get((value or "").strip().lower())


def department_from_category(value):
    return CATEGORY_FROM_CONECTO.get((value or "").strip().lower(), Ticket.Department.SUPPORT)


def category_from_department(department):
    return DEPARTMENT_TO_CATEGORY.get(department, "other")


# --- job status -----------------------------------------------------------
#
# Conecto walks a job through scheduled -> en_route -> checked_in ->
# checked_out/completed. Skybre has four states and no notion of driving.
#
# The two that need stating:
#
#   * EN_ROUTE and CHECKED_IN both become IN_PROGRESS. "On the way" is not
#     a state this platform has, and calling it Scheduled would mean the
#     calendar showed nothing happening while a technician was in the van.
#
#   * CHECKED_OUT is treated as COMPLETED, because Conecto's own spec
#     writes the final step as "checked_out/completed" -- one step under
#     two names. If it turns out they are distinct over there (left site
#     vs. work finished), this is the one line to change, and every raw
#     status is kept on ConectoJobEvent regardless, so nothing is lost by
#     having guessed.

JOB_STATUS_FROM_CONECTO = {
    "scheduled": "scheduled",
    "en_route": "in_progress",
    "checked_in": "in_progress",
    "checked_out": "completed",
    "completed": "completed",
    "cancelled": "cancelled",
}

#: The statuses that mean a technician is actively on this job. Named so a
#: future "where is everyone" screen has one definition to read rather than
#: its own list.
JOB_ACTIVE_STATUSES = ("en_route", "checked_in")


def job_status_from_conecto(value):
    """Unknown values return None so the caller leaves the job alone.

    Same rule as ticket statuses, for the same reason: a status we do not
    recognise must not silently reopen -- or complete -- somebody's work.
    """
    return JOB_STATUS_FROM_CONECTO.get((value or "").strip().lower())


# --- staff roles ----------------------------------------------------------
#
# Skybre's roles and Conecto's are not the same list and never will be:
# ours describe what somebody does in an ISP back office, theirs describe
# what somebody may do in a field app. This is the agreed translation.
#
#   admin, management  -> admin        (they run the business)
#   support, sales,
#   accounts           -> staff        (office roles, no field powers)
#   technician         -> technician   (the one role the app was built for)
#
# Accounts and Sales are here because the alternative -- no app access at
# all -- was considered and rejected: the cost of an accounts clerk having
# a login they never open is lower than the cost of somebody being unable
# to do their job at 7pm because a mapping said no.
STAFF_ROLE_TO_CONECTO = {
    "admin": "admin",
    "management": "admin",
    "support": "staff",
    "sales": "staff",
    "accounts": "staff",
    "technician": "technician",
    # Not a staff role, and listed anyway. A demotion out of the staff
    # roles still pushes -- that push is the REVOCATION, carrying
    # is_active=False (see client.register_staff_code) -- so "customer"
    # arrives here on every one of them. Without this entry it took the
    # unknown-role branch below and logged "add it to
    # STAFF_ROLE_TO_CONECTO" on a perfectly correct revocation, which
    # trained everybody to ignore a warning that is worth reading when it
    # means what it says. The value is irrelevant: is_active=False is what
    # the call is about.
    "customer": "staff",
}


def staff_role_to_conecto(role):
    """Conecto's role for one of ours.

    An unknown role falls back to the least privileged value rather than
    refusing outright: a role added here later should not silently stop
    new staff being able to register. It logs, loudly, because "everyone
    who joined since March is a Conecto staff member regardless of what
    we made them" is exactly the kind of thing that is only noticed when
    somebody cannot do something.
    """
    mapped = STAFF_ROLE_TO_CONECTO.get(role)
    if mapped is None:
        logger.warning(
            "Role %r has no explicit Conecto mapping; sending it as 'staff'. "
            "Add it to STAFF_ROLE_TO_CONECTO.", role,
        )
        return "staff"
    return mapped
