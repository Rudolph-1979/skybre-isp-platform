"""Putting a line back on when the money arrives.

WHAT WAS BROKEN

Nothing reconnected anybody, ever. The chain was cut in two places:

  * billing.recurring._process_blocking suspended a service by setting
    its status and saving, and left no mark saying WHO did it or WHY;
  * the only restore path in the codebase -- customers.signals -- fires
    when a CUSTOMER row goes ACTIVE and restores services flagged
    `auto_suspended_with_customer`, which the billing run never set.

So a customer could pay in full, go into credit, and stay cut off until
somebody noticed and clicked Edit. One did.

THE RULE, AND WHY IT IS THE EXACT INVERSE

blocking_candidate_services suspends on a conjunction: the customer's
balance is worse than their minimum_balance AND an invoice is overdue
past their blocking period. Failing EITHER half fails the whole
predicate, so is_clear() has to check both halves too -- balance alone
is only HALF of what would let someone BACK IN, the same way it is only
half of what put them off.

CORRECTED 1 Oct 2026: is_clear() used to check only `balance <=
minimum_balance`, treating that as "necessary and sufficient" to say the
suspension no longer applies. It is necessary, but on its own it is not
sufficient -- a customer whose old overdue invoice gets credited square,
then billed for a new month before anyone notices, has their balance
back above minimum_balance again with nothing actually overdue. The real
suspension rule (blocking_candidate_services) would not suspend that
customer fresh from this state -- there is no overdue invoice to trigger
it -- but the old balance-only is_clear kept them off regardless, because
it never asked whether anything was actually in arrears, only whether
the account still showed a number above the cushion. Found on a real
account: `has_overdue_invoice` (billing.recurring) is now the shared
second half, called here exactly as blocking_candidate_services calls
it, so the two can no longer independently decide a customer both does
and doesn't still qualify for suspension.

That is deliberately the same two fields read the same way, through the
same shared function. A reactivation rule invented independently would
drift from the suspension rule, and the day they disagree is the day
somebody is reconnected at 15:00 and cut off again at 15:01, or kept off
on a balance the real rule would not have suspended them for in the
first place.

WHAT IS NEVER TOUCHED

A service a human suspended. Abuse, hardware recovery, a dispute, a
customer who asked to be paused -- none of those are undone by money
arriving. `Service.suspended_for_nonpayment` is set only by the billing
run's own blocking pass and cleared the moment anybody changes that
service's status by hand, so a manual suspension can never inherit the
right to auto-reconnect.

WHY .save() AND NOT .update()

Because the signals ARE the enforcement. RADIUS rewrites, the router
block list and dropping the live session all hang off Service's
post_save. A bulk update would mark the row ACTIVE and leave the
customer walled-garden'd in reality -- the mirror image of the bug
customers.signals was written to fix.
"""
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


def _config(customer):
    from .models import CustomerBillingConfig

    return CustomerBillingConfig.for_customer(customer)


def is_clear(customer, config=None):
    """Does this customer still meet the suspension rule?

    False means they do -- they still owe more than their cushion allows
    AND still have a real invoice overdue past their blocking period, so
    they must stay off. Mirrors blocking_candidate_services' full
    conjunction (via the shared has_overdue_invoice), not just its balance
    half -- see this module's docstring for the real account this gap was
    found on.

    `run_date` for the overdue check is always "today": this is called
    outside any billing run (from the Payment signal, or the daily sweep,
    or on demand), so there is no run_date to inherit the way
    blocking_candidate_services' own callers have one.
    """
    from .recurring import has_overdue_invoice

    config = config or _config(customer)
    if customer.balance <= config.minimum_balance:
        return True
    return not has_overdue_invoice(customer, config, timezone.localdate())


def candidates(customer=None, include_unflagged=False):
    """[(service, config)] -- suspended services that should come back on.

    `include_unflagged` is the one-off sweep for services suspended
    before `suspended_for_nonpayment` existed. It cannot tell a billing
    suspension from a manual one, which is exactly why it is opt-in, off
    by default, and why the command that offers it prints the list and
    changes nothing without --commit.
    """
    from customers.models import Customer

    from .models import Service

    services = Service.objects.filter(status=Service.Status.SUSPENDED)
    if not include_unflagged:
        services = services.filter(suspended_for_nonpayment=True)
    if customer is not None:
        services = services.filter(customer=customer)
    services = services.select_related("customer").order_by("customer__customer_id", "pk")

    found = []
    for service in services:
        # Re-read the customer so the balance is the stored one, not a
        # copy from before this payment. Every write to that column goes
        # through .update(balance=F("balance") + ...), which changes the
        # row and leaves any in-memory object holding the old value --
        # the same trap billing.statements.reconciliation fell into.
        owner = Customer.objects.filter(pk=service.customer_id).first()
        if owner is None:
            continue
        config = _config(owner)
        if is_clear(owner, config):
            service.customer = owner
            found.append((service, config))
    return found


def reactivate(service, *, reason="payment received"):
    """Put one service back on, through the normal save path."""
    from .models import Service

    service.status = Service.Status.ACTIVE
    service.suspended_for_nonpayment = False
    # Tells customers.signals this was the billing engine's own doing, so
    # it does not read the change as a human overriding us.
    service._billing_action = True
    service.save()
    logger.info(
        "Service %s reactivated (%s) -- customer %s balance %s",
        service.pk, reason, service.customer_id, service.customer.balance,
    )
    return service


def notify_service_restored(customer):
    """Tell the client their line is back (12 Oct 2026): a "Service restored"
    email, mirrored to their Conecto app. Once per client per reconnection,
    however many services came back. Respects the client's "Send billing
    notifications" switch like the suspension notice does. Never raises."""
    from .models import CustomerBillingConfig

    try:
        config = CustomerBillingConfig.objects.filter(customer=customer).first()
        if config is not None and not config.send_billing_notifications:
            return None
        from notifications.services import send_customer_email

        return send_customer_email("service_restored", customer)
    except Exception:
        logger.exception("Service-restored notice for customer %s failed", getattr(customer, "pk", None))
        return None


def reconnect_customer(customer_id, *, reason="payment received"):
    """Everything this one customer has that should come back on.

    Called from the Payment signal, so it must be cheap and must never
    raise into the caller's transaction -- a reconnection that fails is a
    support call; a payment that fails to record is an accounting
    problem.
    """
    from customers.models import Customer

    customer = Customer.objects.filter(pk=customer_id).first()
    if customer is None:
        return []
    done = []
    for service, _config in candidates(customer=customer):
        try:
            done.append(reactivate(service, reason=reason))
        except Exception:
            logger.exception(
                "Reactivating service %s for customer %s failed", service.pk, customer_id
            )
    if done:
        notify_service_restored(customer)
    return done
