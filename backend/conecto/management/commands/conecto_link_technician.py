"""Record a staff member's Conecto technician code -- or stop their jobs.

The code itself is issued INSIDE Conecto by a Skybre admin -- nothing
here generates one. This only records which of our staff accounts it
belongs to.

WHAT --remove MEANS, NOW THAT STAFF CODES DISPATCH.

It used to mean "forget this code", and that was the same thing as
"stop sending this person jobs", because the code was the only way to
reach them. Since 2026-09-21 every technician is reachable by their staff
code as well, so those two meanings came apart -- and --remove quietly
became a command that printed "deactivated" while the person kept
receiving work.

So --remove now means the second thing, always: this person is off
dispatch until somebody says otherwise. For a technician who has a
hand-set code, that deactivates the link they already have. For one who
only ever had a staff code, it CREATES a link row with no code on it,
switched off, whose only job is to be that "otherwise". Without it, the
command would do nothing at all for exactly the people it is most likely
to be pointed at.

--restore is the way back, and does the obvious thing in both cases.

    python manage.py conecto_link_technician --list
    python manage.py conecto_link_technician christo TECH-001
    python manage.py conecto_link_technician christo --remove
    python manage.py conecto_link_technician christo --restore
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from conecto.models import ConectoTechnician


class Command(BaseCommand):
    help = "Link a staff account to its Conecto technician code, or stop their jobs."

    def add_arguments(self, parser):
        parser.add_argument("username", nargs="?", help="The staff account's username.")
        parser.add_argument("code", nargs="?", help="The technician code issued in Conecto.")
        parser.add_argument("--list", action="store_true", help="Show every linked technician.")
        parser.add_argument(
            "--remove", action="store_true",
            help="Stop sending this person jobs. Works whether or not they have a code.",
        )
        parser.add_argument(
            "--restore", action="store_true",
            help="Undo --remove: put them back on dispatch.",
        )

    def handle(self, *args, **options):
        if options["list"] or not options["username"]:
            self._list()
            return

        User = get_user_model()
        user = User.objects.filter(username=options["username"]).first()
        if user is None:
            self.stderr.write(self.style.ERROR(f'No staff account named "{options["username"]}".'))
            return

        if options["remove"] and options["restore"]:
            self.stderr.write(self.style.ERROR("Pick one of --remove and --restore."))
            return

        if options["remove"]:
            self._remove(user)
            return

        if options["restore"]:
            self._restore(user)
            return

        self._link(user, (options["code"] or "").strip())

    # --- listing ---------------------------------------------------------

    def _list(self):
        links = ConectoTechnician.objects.select_related("user")
        if not links:
            self.stdout.write("No technicians are linked to Conecto yet.")
            return
        self.stdout.write("Linked technicians:")
        for link in links:
            name = link.user.first_name or link.user.username
            code = link.technician_code or "(no code)"
            # "off dispatch" rather than "inactive": the word has to say
            # what it does, because the whole reason this command changed
            # is that "deactivated" read as something it was not doing.
            state = "" if link.is_active else "  -- OFF DISPATCH"
            self.stdout.write(f"  {code:<12} {name} ({link.user.username}){state}")

    # --- stopping and restarting ----------------------------------------

    def _remove(self, user):
        link, created = ConectoTechnician.objects.update_or_create(
            user=user, defaults={"is_active": False},
        )
        self.stdout.write(self.style.SUCCESS(f"{user.username} is off dispatch."))
        if created:
            self.stdout.write(
                "They had no Conecto code, so a blocking record was created for "
                "them. --restore removes it again."
            )
        self.stdout.write(
            "Jobs already on their phone stay there. To take one back, cancel it "
            "or reassign it -- either of those withdraws it through Conecto."
        )

    def _restore(self, user):
        link = ConectoTechnician.objects.filter(user=user).first()
        if link is None:
            self.stdout.write(f"{user.username} was not off dispatch. Nothing to do.")
            return
        if link.technician_code:
            ConectoTechnician.objects.filter(pk=link.pk).update(is_active=True)
            self.stdout.write(self.style.SUCCESS(
                f"{user.username} is back on dispatch, under {link.technician_code}."
            ))
            return
        # A blocking record with no code on it exists only to block. Deleting
        # it is the restore -- leaving it switched on would be a link that
        # links nothing, and the next person to read the list would wonder
        # what it was for.
        link.delete()
        self.stdout.write(self.style.SUCCESS(
            f"{user.username} is back on dispatch, under their staff code "
            f"{user.staff_code or '(none set)'}."
        ))

    # --- linking a code --------------------------------------------------

    def _link(self, user, code):
        if not code:
            self.stderr.write(self.style.ERROR(
                "A technician code is required. Get it from Conecto, or use "
                "--remove / --restore."
            ))
            return

        clash = ConectoTechnician.objects.filter(technician_code=code).exclude(user=user).first()
        if clash is not None:
            # Refused rather than reassigned: two staff sharing a code means
            # jobs for one appearing on the other's phone, and status updates
            # that cannot be told apart afterwards.
            self.stderr.write(self.style.ERROR(
                f'Code "{code}" already belongs to {clash.user.username}. '
                "Remove it there first if it has genuinely moved."
            ))
            return

        link, created = ConectoTechnician.objects.update_or_create(
            user=user, defaults={"technician_code": code, "is_active": True},
        )
        self.stdout.write(self.style.SUCCESS(
            f'{"Linked" if created else "Updated"} {user.username} -> {code}.'
        ))
        self.stdout.write(
            "Jobs assigned to them from now on will appear in Conecto. Jobs already "
            "on the calendar are only pushed when they are next saved."
        )
