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

Latency and packet loss are measured here by ICMP, not read from the API.
Until this was added, `latency_ms` was only ever written by
simulate_monitoring -- so on a real device the Latency column and chart
had a field, a header and no data, which reads as "the device isn't
answering" rather than "nothing has ever measured this".

Measuring from the VPS rather than asking the router to ping something is
deliberate: the number that matters is the round trip between the platform
and the device, over whatever path it currently takes. For the PE routers
that path is now the OpenVPN tunnel, and a latency chart that quietly
stopped reflecting the real path would be worse than no chart.
"""
import re
import subprocess

from django.core.management.base import BaseCommand
from django.utils import timezone

from network import mikrotik
from network.models import Device, MonitoringReading

# Three echoes gives a usable average and a loss figure with some meaning
# (0 / 33 / 67 / 100) without turning an eight-router poll into a minute of
# subprocesses. -W is per-packet, so a fully dead host costs ~3s.
PING_COUNT = 3
PING_TIMEOUT_SECONDS = 1
SUBPROCESS_TIMEOUT_SECONDS = PING_COUNT * PING_TIMEOUT_SECONDS + 3

# "rtt min/avg/max/mdev = 1.562/1.681/1.757/0.085 ms" on Linux, and
# "round-trip min/avg/max" elsewhere -- anchor on the "=" and take the
# second number rather than matching either label.
_RTT_RE = re.compile(r"=\s*[\d.]+/([\d.]+)/[\d.]+")
_LOSS_RE = re.compile(r"([\d.]+)%\s*packet loss")


def ping_stats(ip_address):
    """Returns (latency_ms, packet_loss_pct) for `ip_address`, either of
    which may be None.

    Uses the system `ping` binary rather than a raw ICMP socket for the
    same reason radiusauth.pingcheck does: raw sockets need CAP_NET_RAW,
    which this container doesn't have, while the distro ping binary is
    already set up for it (see backend/Dockerfile, iputils-ping).

    Never raises. A monitoring reading that loses its latency figure is a
    gap in a chart; one that raises takes down the whole poll run and with
    it the CPU, memory and bandwidth for every remaining device.
    """
    try:
        result = subprocess.run(
            ["ping", "-c", str(PING_COUNT), "-W", str(PING_TIMEOUT_SECONDS), "-q", ip_address],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (subprocess.SubprocessError, OSError):
        return None, None

    output = f"{result.stdout}\n{result.stderr}"

    loss = None
    match = _LOSS_RE.search(output)
    if match:
        try:
            loss = float(match.group(1))
        except ValueError:
            loss = None

    latency = None
    match = _RTT_RE.search(output)
    if match:
        try:
            latency = round(float(match.group(1)), 2)
        except ValueError:
            latency = None

    return latency, loss


class Command(BaseCommand):
    help = "Poll every Mikrotik-API-enabled device for real monitoring data."

    def handle(self, *args, **options):
        devices = list(Device.objects.filter(api_enabled=True))
        if not devices:
            self.stdout.write(self.style.WARNING("No devices have the Mikrotik API enabled -- nothing to poll."))
            return

        polled, failed = 0, 0
        for device in devices:
            # Measured before the API call so a device that fails to log in
            # still records how the network looked -- "API rejected us but
            # it answers ICMP in 2ms" and "the box is gone" are different
            # faults and should not look identical on the dashboard.
            latency_ms, packet_loss_pct = ping_stats(device.ip_address)

            try:
                resource = mikrotik.get_system_resource(device)
            except mikrotik.MikrotikError as exc:
                MonitoringReading.objects.create(
                    device=device, timestamp=timezone.now(), is_up=False,
                    latency_ms=latency_ms, packet_loss_pct=packet_loss_pct,
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
            else:
                # Said out loud because a blank WAN interface is the usual
                # reason the Bandwidth column stays empty on a device that
                # is otherwise polling perfectly.
                self.stdout.write(
                    f"{device.name}: no WAN interface set -- bandwidth not read."
                )

            MonitoringReading.objects.create(
                device=device, timestamp=timezone.now(), is_up=True,
                latency_ms=latency_ms, packet_loss_pct=packet_loss_pct,
                cpu_pct=cpu_pct, memory_pct=memory_pct,
                bandwidth_in_mbps=bandwidth_in_mbps, bandwidth_out_mbps=bandwidth_out_mbps,
            )
            device.status = Device.Status.ONLINE
            device.save(update_fields=["status"])
            polled += 1

        self.stdout.write(self.style.SUCCESS(f"Polled {polled} device(s), {failed} unreachable/failed."))
