"""
Renders a FreeRADIUS `clients.conf` snippet from every active
RadiusNasClient row (the Mikrotik(s) staff have added under Networking ->
RADIUS Clients in the admin panel).

**This is now a manual escape hatch, not the normal path.** Saving a
RADIUS client in the admin panel writes the same config to the host-mounted
spool automatically (see radiusauth/clients_conf.py and the post_save
signal in radiusauth/signals.py), and a systemd path unit on the host
validates and installs it. Reach for this command to inspect what would be
written, or to re-spool by hand if the automatic path is broken.

Usage:
  python manage.py render_clients_conf                  # print to stdout
  python manage.py render_clients_conf --output FILE     # write to a file
  python manage.py render_clients_conf --spool           # write the spool file
"""
from django.core.management.base import BaseCommand

from radiusauth.clients_conf import render_clients_conf_text, spool_path, write_clients_conf_spool
from radiusauth.models import RadiusNasClient


class Command(BaseCommand):
    help = "Render a FreeRADIUS clients.conf snippet from RadiusNasClient rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output", type=str, default=None,
            help="Write to this file instead of printing to stdout.",
        )
        parser.add_argument(
            "--spool", action="store_true",
            help="Write to the configured host spool path (RADIUS_CLIENTS_SPOOL), as a save in the admin panel does.",
        )

    def handle(self, *args, **options):
        rendered = render_clients_conf_text()
        count = RadiusNasClient.objects.filter(is_active=True).count()

        if options.get("spool"):
            path = spool_path()
            if not path:
                self.stderr.write(self.style.ERROR(
                    "RADIUS_CLIENTS_SPOOL is not set, so there is nowhere to spool to. "
                    "Set it in the environment (and bind-mount that path from the host) "
                    "or use --output instead."
                ))
                return
            written = write_clients_conf_spool(reason="manual `render_clients_conf --spool`")
            if written:
                self.stdout.write(self.style.SUCCESS(f"Wrote {count} NAS client(s) to the spool at {written}"))
            else:
                self.stderr.write(self.style.ERROR(
                    f"Failed to write the spool at {path} -- see the backend log for why."
                ))
            return

        output_path = options.get("output")
        if output_path:
            with open(output_path, "w") as f:
                f.write(rendered)
            self.stdout.write(self.style.SUCCESS(f"Wrote {count} NAS client(s) to {output_path}"))
        else:
            self.stdout.write(rendered)
