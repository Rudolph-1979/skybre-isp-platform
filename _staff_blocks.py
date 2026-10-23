"""Source blocks inserted by apply_staff_codes.py."""

# ---------------------------------------------------------------------------
# accounts/models.py
# ---------------------------------------------------------------------------

STAFF_CODE_FIELD = '''
    # A stable identifier for a staff member, the way customer_id is one
    # for a customer. Conecto gates staff registration on it: a person can
    # only create an account in the app if we have told Conecto their code
    # and what role it carries.
    #
    # Null rather than blank for the ones that have none. This is unique,
    # and Postgres allows many NULLs in a unique column but only one empty
    # string -- so blank="" would mean the second customer ever created
    # collides with the first.
    #
    # Derived from the primary key rather than allocated sequentially.
    # Sequential allocation means reading the highest code and adding one,
    # which two admins creating staff at the same moment can both do and
    # both get the same answer; the primary key is already unique and
    # already exists. The cost is gaps -- staff share the user table with
    # customers, so the numbers jump -- which is invisible in a code and
    # cheaper than a retry loop around a race nobody would reproduce.
    staff_code = models.CharField(
        max_length=20, unique=True, null=True, blank=True,
        help_text="Identifier shared with the Conecto app. Assigned automatically to staff accounts.",
    )
'''

STAFF_CODE_SAVE = '''    def save(self, *args, **kwargs):
        """Assign a staff code on the way in, once.

        After the first save rather than before it, because the code is
        built from the primary key and there is no key until the row
        exists. That costs one extra UPDATE on the save that creates a
        staff account, and nothing on any save after it.

        Customers are skipped: they already have customer_id, and a
        customer has no business registering in a staff app.
        """
        super().save(*args, **kwargs)
        if self.is_staff_member and not self.staff_code:
            self.staff_code = f"STF-{self.pk:06d}"
            # `using` carried through: create_user() passes it, and a
            # second save that quietly ignored it would write the code to a
            # different database than the row it belongs to.
            super().save(update_fields=["staff_code"], using=kwargs.get("using"))

'''

# ---------------------------------------------------------------------------
# accounts/serializers.py
# ---------------------------------------------------------------------------

SERIALIZER_FIELDS_OLD = '''        fields = ["id", "username", "password", "first_name", "last_name", "email", "phone", "role", "is_active"]
        read_only_fields = ["id"]
'''

SERIALIZER_FIELDS_NEW = '''        fields = [
            "id", "username", "password", "first_name", "last_name", "email",
            "phone", "role", "is_active", "staff_code",
        ]
        # staff_code is assigned by the model, never chosen by an admin --
        # it is what Conecto gates app registration on, so a hand-edited
        # one would either collide with somebody else's or register nobody.
        read_only_fields = ["id", "staff_code"]
'''

# ---------------------------------------------------------------------------
# conecto/mapping.py
# ---------------------------------------------------------------------------

ROLE_MAPPING = '''

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
'''

MAPPING_LOGGER = '''import logging

logger = logging.getLogger(__name__)

'''

# ---------------------------------------------------------------------------
# conecto/client.py
# ---------------------------------------------------------------------------

REGISTER_STAFF = '''def register_staff_code(user):
    """Tell Conecto a staff code exists, what role it carries, and whether
    it is still valid. Safe to call repeatedly -- upsert, like
    register_client_code.

    is_active is the revocation channel. There is no separate delete
    call: one endpoint that can also say "no longer valid" is idempotent,
    survives being replayed by conecto_resync, and cannot leave a code
    half-removed. A staff member who leaves is deactivated in the
    platform and loses app access from the same action -- which is the
    only arrangement that actually holds, because anything requiring a
    second, separate step in another system is a step that eventually
    does not happen.

    A demotion out of the staff roles counts as inactive too: somebody
    turned back into a customer account should not keep a field app.
    """
    from .mapping import staff_role_to_conecto

    if not user.staff_code:
        return None

    payload = {
        "staff_code": user.staff_code,
        "role": staff_role_to_conecto(user.role),
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "email": user.email or "",
        "is_active": bool(user.is_active and user.is_staff_member),
    }
    return call(
        "registerStaffCode", payload,
        subject_kind="staff", subject_ref=user.staff_code,
    )


'''

# ---------------------------------------------------------------------------
# conecto/signals.py
# ---------------------------------------------------------------------------

SIGNALS_IMPORT = '''from django.contrib.auth import get_user_model
'''

STAFF_SIGNALS = '''

# ---------------------------------------------------------------------------
# Staff -> Conecto staff codes
# ---------------------------------------------------------------------------
# Conecto will not let a staff member register in the app unless it knows
# their code and their role. So the code has to reach them when the
# account is made, the role has to reach them when it changes, and -- the
# part that matters most -- the revocation has to reach them the moment
# the account is switched off here.
#
# That last one is why is_active is tracked at all. An ex-employee who
# keeps a field app because somebody forgot to remove them in a second
# system is not a hypothetical; it is the default outcome of any design
# where leaving requires two actions in two places.

User = get_user_model()

#: What Conecto holds a copy of. A change to anything else -- a phone
#: number, a section permission, a password -- is our business alone.
STAFF_TRACKED = ("role", "is_active", "staff_code")


@receiver(pre_save, sender=User, dispatch_uid="conecto_staff_snapshot")
def _snapshot_staff(sender, instance, **kwargs):
    if not instance.pk:
        instance._conecto_staff_before = None
        return
    instance._conecto_staff_before = (
        User.objects.filter(pk=instance.pk).values(*STAFF_TRACKED).first()
    )


@receiver(post_save, sender=User, dispatch_uid="conecto_staff_push")
def _push_staff(sender, instance, created, **kwargs):
    if is_suppressed() or not instance.staff_code:
        return

    before = getattr(instance, "_conecto_staff_before", None)
    was_staff = bool(before) and before.get("role") != User.Role.CUSTOMER

    # Neither a staff account now nor one a moment ago: nothing Conecto
    # has ever been told about, so nothing to tell them.
    if not instance.is_staff_member and not was_staff:
        return

    # Only real changes push. Staff rows are saved by plenty of unrelated
    # code -- a login updating last_login, a section permission edit, a
    # password reset -- and re-sending an unchanged role on each of those
    # is a call that can only do harm if it lands out of order.
    if before is not None and all(
        before.get(field) == getattr(instance, field) for field in STAFF_TRACKED
    ):
        return

    transaction.on_commit(lambda: _send_staff_code(instance.pk))


def _send_staff_code(user_pk):
    """Re-read before sending, for the same reason _create_ticket does:
    on_commit runs after the transaction and the in-memory object may not
    be what landed."""
    user = User.objects.filter(pk=user_pk).first()
    if user is None or not user.staff_code:
        return
    client.register_staff_code(user)
'''

# ---------------------------------------------------------------------------
# settings.py
# ---------------------------------------------------------------------------

SETTINGS_URL = '''    "registerStaffCode": config("CONECTO_URL_REGISTER_STAFF_CODE", default=""),
'''

# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------

MIGRATION = '''"""Staff codes, and one for every staff account that already exists.

The code is what Conecto gates app registration on. Backfilled here
rather than left to be assigned on each account's next save, because
"next save" for a staff member who is not being edited is never, and they
would simply be unable to register with no visible reason why.

Derived from the primary key, matching User.save(). Customers are left
null: they have customer_id, and a unique column takes many NULLs but
only one empty string.
"""
from django.db import migrations, models


def assign_codes(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    # The historical model has no is_staff_member property, so the rule it
    # encodes -- everything except Customer is staff -- is written out.
    for user in User.objects.exclude(role="customer").filter(staff_code__isnull=True):
        user.staff_code = f"STF-{user.pk:06d}"
        user.save(update_fields=["staff_code"])


def clear_codes(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.update(staff_code=None)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "%(head)s"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="staff_code",
            field=models.CharField(
                blank=True, max_length=20, null=True, unique=True,
                help_text="Identifier shared with the Conecto app. Assigned automatically to staff accounts.",
            ),
        ),
        migrations.RunPython(assign_codes, clear_codes),
    ]
'''
