"""
Generates synthetic monitoring readings for every Device that isn't hooked
up to a real Mikrotik API connection.

This stands in for a real SNMP poller on devices without api_enabled=True.
For those, poll_mikrotik_devices pulls real readings instead -- see that
command -- so this one deliberately excludes them to avoid simulated data
overwriting/interleaving with real data on the same device's chart. To
wire up real (non-Mikrotik) SNMP monitoring instead of simulation:
  1. Install an SNMP client (e.g. `pysnmp` or shell out to `snmpget`/`snmpwalk`).
  2. Replace the random values below with real OID queries against
     device.ip_address using device.snmp_community / device.snmp_version
     (e.g. ifInOctets/ifOutOctets for bandwidth, sysUpTime for uptime,
     hrProcessorLoad for CPU).
  3. Run this command on a schedule (cron / celery beat) instead of manually,
     e.g. every 1-5 minutes.
"""
import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from network.models import Device, MonitoringReading


class Command(BaseCommand):
    help = "Simulate SNMP monitoring readings for non-Mikrotik-API devices (dev/demo only)."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=24, help="How many hours of history to backfill.")
        parser.add_argument("--interval-minutes", type=int, default=5, help="Interval between readings.")

    def handle(self, *args, **options):
        devices = list(Device.objects.exclude(api_enabled=True))
        if not devices:
            self.stdout.write(self.style.WARNING("No devices found. Run seed_demo_data first."))
            return

        hours = options["hours"]
        interval = options["interval_minutes"]
        now = timezone.now()
        steps = int((hours * 60) / interval)

        created = 0
        for device in devices:
            base_latency = random.uniform(2, 15)
            base_cpu = random.uniform(15, 45)
            is_flaky = random.random() < 0.15
            for step in range(steps, -1, -1):
                ts = now - timedelta(minutes=step * interval)
                down = is_flaky and random.random() < 0.03
                reading = MonitoringReading(
                    device=device,
                    timestamp=ts,
                    is_up=not down,
                    latency_ms=None if down else round(base_latency + random.uniform(-1.5, 4), 2),
                    packet_loss_pct=100 if down else round(random.uniform(0, 1.2), 2),
                    bandwidth_in_mbps=0 if down else round(random.uniform(5, 180), 2),
                    bandwidth_out_mbps=0 if down else round(random.uniform(2, 90), 2),
                    cpu_pct=round(base_cpu + random.uniform(-5, 20), 2),
                    memory_pct=round(random.uniform(30, 70), 2),
                )
                MonitoringReading.objects.bulk_create([reading])
                created += 1

            last_down = MonitoringReading.objects.filter(device=device).order_by("-timestamp").first()
            device.status = Device.Status.ONLINE if (last_down and last_down.is_up) else Device.Status.OFFLINE
            device.save()

        self.stdout.write(self.style.SUCCESS(f"Created {created} monitoring readings across {len(devices)} devices."))
