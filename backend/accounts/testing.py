"""Test helpers for section access.

WHY THIS EXISTS.

`allowed_sections` used to mean "everything" when empty, so tests across
the platform created a staff user, said nothing about sections, and got
in everywhere. That default has been removed -- it made the whole
permission mechanism opt-in, and left every unconfigured account able to
read payroll and the credential store.

The tests that broke are not tests ABOUT sections. They are about partner
scoping, ledgers, device secrets, salary VAT. Granting them every section
puts them back where they were, testing what they were written to test.
Section access itself is covered by accounts/test_section_access.py, which
is the only place that should be asserting anything about this field.

A test that IS about sections should name the sections it means --
`allowed_sections=["inventory"]` -- and not use this.
"""
from django.contrib.auth import get_user_model

User = get_user_model()

# Derived from the model rather than written out, so a section added to
# User.Section is granted here automatically. A hand-written list would
# quietly stop covering the newest section, and the failure would look
# like the feature being broken rather than the fixture being stale.
ALL_SECTIONS = [value for value, _label in User.Section.choices]


def staff_user(username, role=None, **kwargs):
    """A staff user who can reach every section, for tests that need a
    logged-in member of staff and do not care which one.

    kwargs win, so a caller can still narrow the sections or set
    allowed_partners. Exists so new tests have something obvious to reach
    for instead of rediscovering that an empty list now means nothing.
    """
    kwargs.setdefault("allowed_sections", ALL_SECTIONS)
    kwargs.setdefault("password", "pw-for-tests")
    return User.objects.create_user(
        username=username, role=role or User.Role.SUPPORT, **kwargs
    )
