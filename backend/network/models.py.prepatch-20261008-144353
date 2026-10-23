from django.db import models


class NetworkSite(models.Model):
    """A physical tower/site location that hardware (Device rows) can be
    mounted at -- one level above an individual router. A site can host many
    devices (see Device.site below); the reseller Partners it serves are
    optional and purely informational/filtering at this stage, unlike
    Device.visible_partners which actually restricts staff access."""

    title = models.CharField(max_length=150)
    contact_person = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    address = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=255, blank=True, help_text="Free-text region/area, for filtering the site list.")
    # Was a single ForeignKey. A tower routinely serves more than one
    # reseller, and forcing a choice between them meant the site list could
    # not answer "which of our sites does this partner have customers on" --
    # the one question it exists to answer.
    #
    # Same convention as Device.visible_partners and User.allowed_partners:
    # empty means every partner, not none.
    partners = models.ManyToManyField(
        "customers.Partner", blank=True, related_name="network_sites",
        help_text="Partners served from this site. Leave empty for all.",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title


class Device(models.Model):
    class DeviceType(models.TextChoices):
        ROUTER = "router", "Router"
        SWITCH = "switch", "Switch"
        OLT = "olt", "OLT"
        AP = "access_point", "Access Point"
        SERVER = "server", "Server"
        ONU = "onu", "ONU/CPE"

    class Status(models.TextChoices):
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"
        UNKNOWN = "unknown", "Unknown"

    name = models.CharField(max_length=150)
    device_type = models.CharField(max_length=20, choices=DeviceType.choices, default=DeviceType.ROUTER)
    ip_address = models.GenericIPAddressField()
    location = models.CharField(max_length=255, blank=True)
    site = models.ForeignKey(
        NetworkSite, on_delete=models.SET_NULL, null=True, blank=True, related_name="devices",
        help_text="Physical site/tower this device is mounted at, if tracked (see Networking -> Sites).",
    )
    vendor = models.CharField(max_length=100, blank=True)
    model_name = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNKNOWN)
    # Which reseller partners' staff can see/manage this specific device --
    # same empty-means-unrestricted convention as accounts.User.
    # allowed_sections/allowed_partners: empty = every partner's staff can
    # see it (the default). Restricting to specific partners hides this
    # device from any staff member whose own account is itself restricted
    # to a different set of partners (see DeviceViewSet.get_queryset).
    visible_partners = models.ManyToManyField(
        "customers.Partner", blank=True, related_name="visible_devices",
        help_text="Partners whose staff can see this device. Empty = visible to everyone with Networking access.",
    )
    snmp_community = models.CharField(
        max_length=100, blank=True, default="public",
        help_text="SNMP read community string. Wire real polling to network.monitoring against this field.",
    )
    snmp_version = models.CharField(max_length=10, default="2c")
    created_at = models.DateTimeField(auto_now_add=True)

    # --- Mikrotik RouterOS API -------------------------------------------
    # Optional, per-device. When enabled, network/mikrotik.py talks to this
    # device's real RouterOS API instead of (or alongside) the simulated
    # SNMP-style monitoring -- see the poll_mikrotik_devices management
    # command, and the test-connection/poll-now/live-sessions/
    # disconnect-session actions on DeviceViewSet. api_password is
    # intentionally recoverable (not hashed) since it's sent to the router
    # on every API call, same reasoning as billing.Service.radius_password
    # -- but still write-only/masked on this platform's own REST API.
    api_enabled = models.BooleanField(
        default=False,
        help_text="Talk to this device's real RouterOS API (monitoring, live sessions, config push) instead of only simulated readings.",
    )
    api_port = models.PositiveIntegerField(default=8728, help_text="RouterOS API port -- 8728 plaintext, 8729 with SSL.")
    api_username = models.CharField(max_length=100, blank=True)
    api_password = models.CharField(max_length=255, blank=True, null=True)
    api_use_ssl = models.BooleanField(default=False)
    api_wan_interface = models.CharField(
        max_length=64, blank=True,
        help_text="Interface name to read bandwidth from, e.g. ether1 or ovpn-server1. Leave blank to skip bandwidth polling.",
    )

    # --- Live-API config push (blocking / shaper / wireless) --------------
    # These toggles gate the newer "live API" actions (see
    # network.router_sync and DeviceViewSet's sync-blocking-rules/
    # sync-shaper-queues/wireless-access-list/delete-all-rules actions).
    # Everything pushed under these is tagged with a "skybre-auto-..."
    # comment on the router so it can always be found and removed again
    # without ever touching config staff added by hand -- see
    # network/mikrotik.py's module docstring constants.
    block_disabled_customers = models.BooleanField(
        default=False,
        help_text=(
            "When on, suspended/terminated/pending customers' Services on this device are "
            "automatically pushed into a router-side address-list + firewall drop rule, cutting "
            "off their traffic. Toggle off (then Sync) to remove that address-list/rule from the router."
        ),
    )
    enable_shaper = models.BooleanField(
        default=False,
        help_text=(
            "When on, every active Service on this device gets a real RouterOS Simple Queue "
            "bandwidth limit pushed to the router (its tariff speed, or a Connection Rule override "
            "if one is assigned). Toggle off (then Sync) to remove those queues from the router."
        ),
    )
    shaping_type = models.CharField(
        max_length=20, default="simple_queue",
        help_text="Which RouterOS mechanism the shaper uses. Only 'simple_queue' is implemented for now.",
    )
    enable_wireless_access_list = models.BooleanField(
        default=False,
        help_text="Allow managing this device's RouterOS wireless Access List from the Networking page.",
    )
    enable_mpsk = models.BooleanField(
        default=False,
        help_text="Allow setting a per-client WPA2 passphrase (MPSK) on wireless Access List entries for this device.",
    )
    wireless_interface = models.CharField(
        max_length=64, blank=True,
        help_text="Wireless interface name (e.g. wlan1) the Access List/MPSK actions apply to.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.ip_address})"


class IPPool(models.Model):
    class PoolType(models.TextChoices):
        IPV4 = "ipv4", "IPv4"
        IPV6 = "ipv6", "IPv6"

    class Category(models.TextChoices):
        CUSTOMER = "customer", "Customer IP Pool"
        NETWORK = "network", "Net IP Pool"
        # No real internet route -- suspended PPPoE customers are handed an
        # address from here instead of their normal Customer Pool address
        # while suspended (see radiusauth.signals._allocate_walled_garden_ip).
        # Point the router's routing/firewall at this subnet to show a
        # captive "please pay" page or just drop everything, as you prefer.
        WALLED_GARDEN = "walled_garden", "Walled Garden (no internet)"

    # Purely organizational (Splynx's "RootNet"/"EndNet" concept) -- lets a
    # broader allocation be represented as a parent net with smaller
    # sub-nets carved out of it underneath, for readability/reporting only.
    # Deliberately independent of `category` below: nothing that actually
    # allocates addresses (radiusauth.signals, billing.serializers) reads
    # network_type or root_net, so this can't affect existing PPPoE/OVPN IP
    # assignment behavior.
    class NetworkType(models.TextChoices):
        ENDNET = "endnet", "EndNet"
        ROOTNET = "rootnet", "RootNet"

    name = models.CharField(max_length=150)
    network_cidr = models.CharField(max_length=64, help_text="e.g. 10.10.0.0/24")
    gateway = models.GenericIPAddressField(null=True, blank=True)
    pool_type = models.CharField(max_length=10, choices=PoolType.choices, default=PoolType.IPV4)
    # Customer pools work exactly as they always have -- addresses handed
    # out to customer Services. Network pools are new: FreeRADIUS draws
    # Framed-IP-Address from these to hand to authenticated OVPN clients on
    # the Mikrotik (see radiusauth.signals) -- existing pools default to
    # "customer" on migration since that's the only thing they were ever
    # used for before this field existed.
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.CUSTOMER)
    network_type = models.CharField(max_length=10, choices=NetworkType.choices, default=NetworkType.ENDNET)
    root_net = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="child_nets",
        help_text="Optional parent network this pool is carved out of, for grouping/reporting only.",
    )
    description = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.name} ({self.network_cidr})"


class IPAddress(models.Model):
    class Status(models.TextChoices):
        FREE = "free", "Free"
        ASSIGNED = "assigned", "Assigned"
        RESERVED = "reserved", "Reserved"

    pool = models.ForeignKey(IPPool, on_delete=models.CASCADE, related_name="addresses")
    address = models.GenericIPAddressField(unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.FREE)
    assigned_service = models.ForeignKey(
        "billing.Service", on_delete=models.SET_NULL, null=True, blank=True, related_name="ip_addresses"
    )

    class Meta:
        ordering = ["address"]
        verbose_name_plural = "IP addresses"

    def __str__(self):
        return self.address


class ConnectionRule(models.Model):
    """A named speed-shaping profile scoped to one router (Splynx's
    "Connection rules" tab): Speed Down/Up in kbps plus a guaranteed
    percentage floor. Data-model/CRUD only for now -- nothing here is
    pushed to the router's live RouterOS shaper queues yet (that's
    deliberately on the backburner alongside the rest of the live-API
    work); a rule isn't applied to a Service automatically."""

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="connection_rules")
    title = models.CharField(max_length=150)
    speed_down_kbps = models.PositiveIntegerField(default=0)
    speed_up_kbps = models.PositiveIntegerField(default=0)
    guaranteed_pct = models.PositiveIntegerField(
        default=0, help_text="Guaranteed speed floor, as a percentage of the speeds above."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return f"{self.title} ({self.device.name})"


class MonitoringReading(models.Model):
    """Point-in-time health/performance reading for a device.

    In production this row is populated by a scheduled task that polls the
    device over SNMP (see docs/NETWORK_MONITORING.md). For this build, a
    management command (`simulate_monitoring`) generates realistic synthetic
    readings so the dashboards and charts have live data to render.
    """

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="readings")
    timestamp = models.DateTimeField(db_index=True)
    is_up = models.BooleanField(default=True)
    latency_ms = models.FloatField(null=True, blank=True)
    packet_loss_pct = models.FloatField(null=True, blank=True)
    bandwidth_in_mbps = models.FloatField(null=True, blank=True)
    bandwidth_out_mbps = models.FloatField(null=True, blank=True)
    cpu_pct = models.FloatField(null=True, blank=True)
    memory_pct = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [models.Index(fields=["device", "-timestamp"])]

    def __str__(self):
        return f"{self.device} @ {self.timestamp}"
