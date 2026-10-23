from rest_framework.permissions import BasePermission


class IsStaffMember(BasePermission):
    """Allows access only to internal (non-customer) users -- any of
    admin/support/sales/technician/management/accounts."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_staff_member)


class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == user.Role.ADMIN)


class IsCustomer(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == user.Role.CUSTOMER)


class IsManagement(BasePermission):
    """Management or Admin -- for approval steps a senior role signs off
    on, like deciding a credit request. Admin is always included as the
    system-wide override, same as everywhere else in this app."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role in (user.Role.MANAGEMENT, user.Role.ADMIN))


class IsAccounts(BasePermission):
    """Accounts or Admin -- for submitting finance actions like credit
    requests."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role in (user.Role.ACCOUNTS, user.Role.ADMIN))


class IsNOC(BasePermission):
    """Can decide a ticket escalation -- User.is_noc, or Admin as the
    system-wide override used everywhere else in this app. Deliberately
    not role-based: NOC is a capability a staff member (typically, but
    not necessarily, Support) is individually granted, not a Role of its
    own -- see User.is_noc's docstring."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and (user.is_noc or user.role == user.Role.ADMIN))


class IsAccountsOrManagement(BasePermission):
    """Accounts, Management, or Admin -- who can see/work with credit
    requests at all. Narrower than IsStaffMember since this is financial
    approval data, not something every staff role needs visibility into."""

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.role in (user.Role.ACCOUNTS, user.Role.MANAGEMENT, user.Role.ADMIN)
        )


def user_can_access_section(user, section):
    """Whether `user` can see/use the given section key (one of
    User.Section's values -- scheduling, customers, services, finance,
    inventory, networking, tickets, staff, vehicles, bulk_email, configs).

    Section restrictions are a staff-only concept -- customers never hit
    this check (their own view logic already scopes what they can see).
    Admin always passes, regardless of allowed_sections, so a restriction
    can never accidentally lock the Admin account out of part of the app.

    AN EMPTY LIST MEANS NO SECTIONS. It used to mean unrestricted, for
    backward compatibility with accounts predating the feature -- which
    was reasonable for that one migration and wrong as a standing rule,
    because empty is also what every NEW account starts with. The effect
    was that this whole mechanism was opt-in: any staff account nobody had
    deliberately narrowed could open Accountant (every salary, the VAT
    returns, the bank feeds) and Configs (SMTP, RADIUS and bank API
    credentials). On this database that was nine of nine non-admin staff,
    including three technicians.

    Migration 00xx_backfill_allowed_sections gives every account that
    existed at the time an explicit list for its role, so flipping this
    took nobody's access away. From here a new account starts with
    nothing and is granted what it needs, which is the only default that
    fails safe.
    """
    if not user or not user.is_authenticated:
        return False
    if not user.is_staff_member:
        return True
    if user.role == user.Role.ADMIN:
        return True
    return section in (user.allowed_sections or [])


def section_permission(section):
    """Factory returning a DRF permission class gating one section key.
    Use alongside IsStaffMember (or IsAdmin/etc.), not instead of it --
    this only narrows access further for staff who lack the section, it
    doesn't grant staff-level access on its own."""

    class _SectionPermission(BasePermission):
        message = f"You don't have access to the '{section}' section."

        def has_permission(self, request, view):
            return user_can_access_section(request.user, section)

    _SectionPermission.__name__ = f"Has{section.title().replace('_', '')}SectionAccess"
    return _SectionPermission
