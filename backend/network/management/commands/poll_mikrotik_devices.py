"""
Polls every Device with api_enabled=True over its real RouterOS API and
stores a real MonitoringReading -- the scheduled counterpart to the
"Poll Now" button (DeviceViewSet.poll_now), and the real-data counterpart
to simulate_monitoring (which deliberately skips these devices).

Intended to run on a schedule (cron / celery beat), e.g. every 1-5 minutes:

    */5 * * * * cd /path/to/backend && python manage.py poll_mikrotik_devices

A device that's unreachable or rejects the login is recorded as a down
reading (is_up=False) rather than skipped outright, so an outage still
shows up on the dashboard instead of just leaving a gap.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from network import mikrotik
from network.models import Device, MonitoringReading


class Command(BaseCommand):
    help = "Poll every Mikrotik-API-enabled device for real monitoring data."

    def handle(self, *args, **options):
        devices = list(Device.objects.filter(api_enabled=True))
        if not devices:
            self.stdout.write(self.style.WARNING("No devices have the Mikrotik API enabled -- nothing to poll."))
            return

        polled, failed = 0, 0
        for device in devices:
            try:
                resource = mikrotik.get_system_resource(device)
            except mikrotik.MikrotikError as exc:
                MonitoringReading.objects.create(
                    device=device, timestamp=timezone.now(), is_up=False,
                )
                device.status = Device.Status.OFFLINE
                device.save(update_fields=["status"])
                failed += 1
                self.stdout.write(self.style.WARNING(f"{device.name}: {exc}"))
                continue

            cpu_pct = resource.get("cpu-load")
            memory_pct = None
            total_mem, free_mem = resource.get("total-memory"), resource.get("free-memory")
            if total_mem and free_mem is not None:
                try:
                    memory_pct = round((1 - float(free_mem) / float(total_mem)) * 100, 1)
                except (TypeError, ValueError, ZeroDivisionError):
                    memory_pct = None

            bandwidth_in_mbps = bandwidth_out_mbps = None
            if device.api_wan_interface:
                try:
                    bandwidth_in_mbps, bandwidth_out_mbps = mikrotik.get_wan_interface_traffic(
                        device, device.api_wan_interface
                    )
                except mikrotik.MikrotikError as exc:
                    self.stdout.write(self.style.WARNING(f"{device.name}: bandwidth read failed -- {exc}"))

            MonitoringReading.objects.create(
                device=device, timestamp=timezone.now(), is_up=True,
                cpu_pct=cpu_pct, memory_pct=memory_pct,
                bandwidth_in_mbps=bandwidth_in_mbps, bandwidth_out_mbps=bandwidth_out_mbps,
            )
            device.status = Device.Status.ONLINE
            device.save(update_fields=["status"])
            polled += 1

        self.stdout.write(self.style.SUCCESS(f"Polled {polled} device(s), {failed} unreachable/failed."))
