"""Push every staff member's code and role to Conecto.

Needed twice. Once when Conecto's registerStaffCode endpoint first goes
live, because every staff account that already exists has a code Conecto
has never been told about and none of those people could otherwise
register. And again after any change to how the mapping is DERIVED --
moving Sales from staff to technician, say -- which the per-save signal
would only apply to accounts that happen to be edited afterwards, leaving
everyone else on the old role indefinitely.

Reports before/after per staff member and sends nothing without --commit.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from conecto import client
from conecto.mapping import staff_role_to_conecto
from conecto.models import ConectoCall

User = get_user_model()


class Command(BaseCommand):
    help = "Sends every staff account's code and role to Conecto."

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit", action="store_true",
            help="Actually send. Without this, only reports what would be sent.",
        )
        parser.add_argument(
            "--include-inactive", action="store_true",
            help=(
                "Also send deactivated staff, as revocations. Worth doing on the "
                "first run: somebody who left before this existed may still have "
                "an app account nobody has taken away."
            ),
        )

    def handle(self, *args, **options):
        commit = options["commit"]
        staff = User.objects.exclude(role=User.Role.CUSTOMER).order_by("username")
        if not options["include_inactive"]:
            staff = staff.filter(is_active=True)

        if not staff.exists():
            self.stdout.write("No staff accounts.")
            return

        if not client._url("registerStaffCode"):
            self.stdout.write(self.style.WARNING(
                "CONECTO_URL_REGISTER_STAFF_CODE is not set, so nothing can be sent. "
                "Conecto has to publish the endpoint first."
            ))
            if commit:
                return

        sent = failed = skipped = 0
        for user in staff:
            if not user.staff_code:
                # Only possible for a row written by something that bypassed
                # save() -- a bulk_create, or a fixture. Worth naming rather
                # than silently passing over: that person cannot register.
                self.stdout.write(self.style.WARNING(
                    f"  {user.username}: NO staff code -- cannot register in the app"
                ))
                skipped += 1
                continue

            role = staff_role_to_conecto(user.role)
            state = "active" if user.is_active else "REVOKED"
            self.stdout.write(f"  {user.username}: {user.staff_code} -> {role} ({state})")

            if commit:
                before = ConectoCall.objects.filter(
                    endpoint="registerStaffCode", subject_ref=user.staff_code,
                ).count()
                client.register_staff_code(user)
                after = ConectoCall.objects.filter(
                    endpoint="registerStaffCode", subject_ref=user.staff_code,
                    status=ConectoCall.Status.OK,
                ).count()
                if after > before:
                    sent += 1
                else:
                    failed += 1

        self.stdout.write("")
        if not commit:
            self.stdout.write("Dry run. Re-run with --commit to send.")
            return

        self.stdout.write(self.style.SUCCESS(f"Sent {sent} staff code(s)."))
        if failed:
            self.stdout.write(self.style.WARNING(
                f"{failed} failed -- see Conecto call records, and replay with conecto_resync."
            ))
        if skipped:
            self.stdout.write(self.style.WARNING(f"{skipped} had no staff code."))
