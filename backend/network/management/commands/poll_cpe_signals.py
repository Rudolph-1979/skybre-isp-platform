"""Read every client CPE's signal from the MikroTik APs (network.cpe_signals).

Every 5 minutes:

    */5 * * * * cd /home/ubuntu/isp-platform && docker compose exec -T backend \\
        python manage.py poll_cpe_signals >/dev/null 2>&1
"""
from django.core.management.base import BaseCommand

from network.cpe_signals import poll_all


class Command(BaseCommand):
    help = "Read client CPE signal from the MikroTik APs' wireless registration tables."

    def handle(self, *args, **options):
        result = poll_all()
        self.stdout.write(
            f"{result['stations']} stations on the APs · {result['matched']} of {result['services']} "
            f"active lines matched · {result['failed']} device(s) not reachable"
        )
