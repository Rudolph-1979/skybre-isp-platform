from django.contrib.postgres.fields import ArrayField
from django.db import models


class RadCheck(models.Model):
    """Mirrors FreeRADIUS's stock `radcheck` table verbatim (same table/
    column names as the out-of-the-box postgresql schema.sql + queries.conf
    that ships with FreeRADIUS) so the server's default SQL module config
    works against this table with zero custom queries.conf edits.

    Rows here are NOT edited directly by staff -- they're kept in sync from
    billing.Service.radius_username/radius_password by the signals in
    signals.py whenever a service's RADIUS credentials change. One
    Cleartext-Password check row per service with RADIUS login enabled.
    """

    username = models.CharField(max_length=255, db_index=True)
    attribute = models.CharField(max_length=255)
    op = models.CharField(max_length=2, default="==")
    value = models.CharField(max_length=255)

    class Meta:
        db_table = "radcheck"

    def __str__(self):
        return f"{self.username}: {self.attribute} {self.op} {self.value}"


class RadReply(models.Model):
    """Mirrors FreeRADIUS's stock `radreply` table. Kept in sync the same
    way as RadCheck -- one Framed-IP-Address row (the customer's assigned
    Net IP Pool address) and one Mikrotik-Rate-Limit row (derived from the
    service's tariff download/upload speed) per RADIUS-enabled service."""

    username = models.CharField(max_length=255, db_index=True)
    attribute = models.CharField(max_length=255)
    op = models.CharField(max_length=2, default="=")
    value = models.CharField(max_length=255)

    class Meta:
        db_table = "radreply"

    def __str__(self):
        return f"{self.username}: {self.attribute} {self.op} {self.value}"


class RadAcct(models.Model):
    """Mirrors FreeRADIUS's stock `radacct` table. FreeRADIUS itself
    writes/updates rows here directly (Accounting-Start / Interim-Update /
    Accounting-Stop) -- this app never writes to it, only reads it, to
    power the "live sessions" / session history view in Networking."""

    radacctid = models.BigAutoField(primary_key=True)
    acctsessionid = models.CharField(max_length=64)
    acctuniqueid = models.CharField(max_length=32, unique=True)
    username = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    groupname = models.CharField(max_length=255, null=True, blank=True)
    realm = models.CharField(max_length=64, null=True, blank=True)
    nasipaddress = models.GenericIPAddressField()
    nasportid = models.CharField(max_length=32, null=True, blank=True)
    nasporttype = models.CharField(max_length=32, null=True, blank=True)
    acctstarttime = models.DateTimeField(null=True, blank=True)
    acctupdatetime = models.DateTimeField(null=True, blank=True)
    acctstoptime = models.DateTimeField(null=True, blank=True)
    acctinterval = models.BigIntegerField(null=True, blank=True)
    acctsessiontime = models.BigIntegerField(null=True, blank=True)
    acctauthentic = models.CharField(max_length=32, null=True, blank=True)
    connectinfo_start = models.CharField(max_length=255, null=True, blank=True)
    connectinfo_stop = models.CharField(max_length=255, null=True, blank=True)
    acctinputoctets = models.BigIntegerField(null=True, blank=True)
    acctoutputoctets = models.BigIntegerField(null=True, blank=True)
    calledstationid = models.CharField(max_length=255, null=True, blank=True)
    callingstationid = models.CharField(max_length=255, null=True, blank=True)
    acctterminatecause = models.CharField(max_length=64, null=True, blank=True)
    servicetype = models.CharField(max_length=64, null=True, blank=True)
    framedprotocol = models.CharField(max_length=32, null=True, blank=True)
    framedipaddress = models.GenericIPAddressField(null=True, blank=True)
    framedipv6address = models.GenericIPAddressField(null=True, blank=True)
    framedipv6prefix = models.CharField(max_length=64, null=True, blank=True)
    framedinterfaceid = models.CharField(max_length=64, null=True, blank=True)
    delegatedipv6prefix = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        db_table = "radacct"
        ordering = ["-acctstarttime"]
        indexes = [models.Index(fields=["username", "-acctstarttime"])]

    def __str__(self):
        return f"{self.username} @ {self.nasipaddress} ({self.acctstarttime})"

    @property
    def is_active(self):
        return self.acctstoptime is None


class SessionUsageSnapshot(models.Model):
    """Derived live throughput for one RADIUS session.

    `radacct` carries CUMULATIVE byte counters and FreeRADIUS UPDATEs the
    same row on every interim, so the table can tell you how much a session
    has used but never how fast it is going right now -- the previous value
    is overwritten. This holds that missing previous value so a rate can be
    worked out.

    One row per session, updated in place by the `sample_session_usage`
    management command, so this table stays the same size as the number of
    sessions rather than growing per sample. That is deliberate: a
    per-minute time series across a few hundred customers would be
    hundreds of thousands of rows a day for a number that is only ever
    looked at live.

    Nothing here polls a router. The NAS pushes its counters to FreeRADIUS
    on its accounting interval; this only reads the database.
    """

    # radacct's own unique key for a session, so a customer reconnecting
    # gets a fresh baseline instead of inheriting the old session's rate.
    acctuniqueid = models.CharField(max_length=32, unique=True)
    username = models.CharField(max_length=255, db_index=True)

    # The last counter values we actually saw CHANGE, and when. Not simply
    # "the previous sample": with a 5-minute accounting interval and a
    # 1-minute sampler, four samples in five show no change at all, and
    # dividing by the sampling interval instead of the interval over which
    # the bytes really accrued would report a spike followed by four zeros.
    last_input_octets = models.BigIntegerField(default=0)
    last_output_octets = models.BigIntegerField(default=0)
    last_change_at = models.DateTimeField()

    # Bits per second over the period the counters last advanced.
    input_bps = models.BigIntegerField(default=0)
    output_bps = models.BigIntegerField(default=0)

    sampled_at = models.DateTimeField()

    # No Meta.indexes here: `username` above is already db_index=True and
    # `acctuniqueid` is unique, so both lookups this table serves are
    # covered. Declaring an index for username as well created a second,
    # identical one.

    def __str__(self):
        return f"{self.username} {self.input_bps}/{self.output_bps} bps"


class RouterLiveRate(models.Model):
    """Live throughput for one session, read from the ROUTER rather than
    derived from accounting.

    Two sources of "how fast is this line going" exist, and they answer
    different questions:

    * SessionUsageSnapshot derives a rate from RADIUS accounting. Free, no
      router involvement, scales to every customer -- but it is an AVERAGE
      over the NAS's reporting interval, so a 20-second speed test on a
      1-minute interim reads about a third of its peak.
    * This is what the line is doing right now, from the router's own
      interface counters.

    The expensive way to get this would be one API call per customer being
    looked at. Instead `poll_live_traffic` makes ONE call per router that
    returns every session, so the cost is per-router-per-poll and does not
    grow with customers or viewers.

    Rows are keyed on username because that is what both the router's
    session list and RADIUS agree on.
    """

    username = models.CharField(max_length=255, unique=True)
    device = models.ForeignKey(
        "network.Device", on_delete=models.CASCADE, related_name="live_rates", null=True, blank=True
    )
    interface = models.CharField(max_length=64, blank=True)

    # Cumulative counters from the last poll, and when. RouterOS reports
    # totals, so a rate needs two readings.
    last_rx_byte = models.BigIntegerField(default=0)
    last_tx_byte = models.BigIntegerField(default=0)

    # Customer-facing directions: download = router -> client (RouterOS tx).
    download_bps = models.BigIntegerField(default=0)
    upload_bps = models.BigIntegerField(default=0)

    sampled_at = models.DateTimeField()

    # No Meta.indexes: `username` is unique above, and a unique constraint
    # is an index. A second one on the same column buys nothing and costs a
    # write on every poll.

    def __str__(self):
        return f"{self.username} {self.download_bps}/{self.upload_bps} bps (router)"


class RadiusNasClient(models.Model):
    """A Mikrotik (or other NAS) device allowed to talk to this FreeRADIUS
    server, plus its shared secret. Managed here for a friendly admin UI --
    FreeRADIUS itself doesn't read this table live; `render_clients_conf`
    (see management/commands/) turns these rows into a `clients.conf`
    snippet staff copy onto the FreeRADIUS server and reload, since that's
    the standard/supported way FreeRADIUS learns which NAS devices to
    accept requests from and with which secret."""

    name = models.CharField(max_length=150, help_text="Friendly name, e.g. 'Teraco JHB Mikrotik'.")
    ip_address = models.GenericIPAddressField(help_text="The Mikrotik's IP address as seen by the FreeRADIUS server.")
    shortname = models.CharField(max_length=64, help_text="Short identifier used in FreeRADIUS logs, e.g. 'mikrotik-jhb'.")
    secret = models.CharField(max_length=128, help_text="Shared RADIUS secret configured identically on the Mikrotik.")
    # Organizational/reporting tag only -- e.g. a site or region name. This
    # platform runs a single FreeRADIUS server (SQL-backed by radcheck/
    # radreply), so setting this does NOT change how requests are actually
    # authenticated or routed; it just lets a NAS's realm be shown/filtered
    # on the Live Sessions view (matched by NAS IP -- see
    # RadAcctSerializer.get_realm) and noted in the generated clients.conf
    # for staff's own reference. A real FreeRADIUS proxy.conf-style realm
    # (routing to a genuinely separate RADIUS home server) would be a
    # bigger, separate feature -- this isn't that.
    realm = models.CharField(
        max_length=64, blank=True,
        help_text="Optional site/region tag (e.g. 'jhb') for reporting/segmentation only -- does not change live routing.",
    )
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.ip_address})"


class OvpnSettings(models.Model):
    """Platform-wide OVPN/FreeRADIUS defaults, edited from Configs -> OVPN.
    A singleton row (always pk=1, see load()) -- same convention as
    notifications.EmailSettings -- rather than a ModelViewSet, since
    there's only ever one FreeRADIUS server for this platform to point at.

    Today this only holds the FreeRADIUS server's own address, which used
    to be typed by hand into the "Push to router" modal on every single
    push (see RadiusNasClientViewSet.push_to_router) with nothing
    persisted anywhere -- staff can set it once here and the frontend
    pre-fills it from then on, while still allowing an override per push
    for a one-off/secondary FreeRADIUS server."""

    freeradius_ip = models.CharField(
        max_length=255, blank=True,
        help_text="Default FreeRADIUS server IP/hostname, pre-filled into the 'Push to router' action under Networking -> RADIUS Clients.",
    )
    notes = models.TextField(
        blank=True, help_text="Free-text notes for staff -- e.g. links to RADIUS_SETUP.md, reminders about clients.conf.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "OVPN settings"


class OvpnClientConnection(models.Model):
    """An outbound OpenVPN *client* tunnel this platform's own VPS dials
    out on to reach a router's private management network -- e.g. a
    connection named "skybre" dialing to a Mikrotik's private management
    IP so the platform can reach RADIUS/API traffic on that private
    network without the router needing a second public IP. Modeled
    directly on Splynx's own Config -> Tools -> VPN -> OpenVPN page,
    which is the reference this was built from (2026-08-19).

    This is a config record only -- staff-managed here, then downloaded
    (see OvpnClientConnectionViewSet.config) and installed as a
    systemd-managed openvpn-client@<name> service ON THE VPS HOST.
    Nothing in this Django app starts, stops, or otherwise controls an
    actual OS-level OpenVPN process -- same reason FreeRADIUS itself
    runs on the host rather than in the backend container (see
    render_clients_conf's docstring). "Status" on the Networking page is
    a live ping to remote_ip (see radiusauth.pingcheck), which confirms
    the remote endpoint is reachable on the network, not that this
    specific tunnel is actually up.
    """

    name = models.CharField(
        max_length=100, unique=True,
        help_text="Connection name, e.g. 'skybre' -- becomes the systemd unit name (openvpn-client@<name>) once installed.",
    )
    comment = models.CharField(max_length=255, blank=True)
    remote_ip = models.CharField(max_length=255, help_text="Remote OpenVPN server address (IP or hostname).")
    remote_port = models.PositiveIntegerField(default=1194)
    username = models.CharField(max_length=150, blank=True)
    # Write-only via the serializer (never echoed back) -- same
    # write-only-secret/`_set` companion-field convention as
    # RadiusNasClient.secret.
    password = models.CharField(max_length=255, blank=True)
    # One "<network> <netmask> <gateway>" triple per line -- kept as free
    # text rather than a structured table since that's exactly the format
    # Splynx's own reference UI enters/displays them in, and how they're
    # rendered into the downloadable client config's `route` lines below.
    routes = models.TextField(
        blank=True,
        help_text="One route per line: <network> <netmask> <gateway>, e.g. '10.0.0.0 255.0.0.0 10.250.32.2'.",
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text="Staff-facing intent only -- toggling this does not itself start/stop anything on the VPS; it's a reminder of whether this tunnel is supposed to be active.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} -> {self.remote_ip}:{self.remote_port}"


class RadiusAction(models.Model):
    """A record of every attempt to push a change out to a live session.

    This table exists because its absence cost weeks. Enforcement ran in a
    post-commit background thread, so by the time it failed the HTTP response
    had already gone out reporting success; the only trace was a warning in
    the container log that nobody was watching. Staff saw "Saved", the
    customer saw no change, and there was nothing on screen connecting the
    two. Every outcome now lands here -- including the ones where there was
    correctly nothing to do, since "nothing needed doing" and "we couldn't
    reach the router" are indistinguishable from the outside and want
    completely different responses.

    Kept when a service is deleted: the history of what was done to a
    customer's connection outlives the row describing their subscription.
    """

    class Action(models.TextChoices):
        COA_RATE = "coa_rate", "Change speed on live session"
        DISCONNECT = "disconnect", "Disconnect live session"
        NONE = "none", "Nothing to do"

    class Transport(models.TextChoices):
        COA = "coa", "RADIUS CoA (RFC 5176)"
        API = "api", "RouterOS API"
        NONE = "none", "Not attempted"

    service = models.ForeignKey(
        "billing.Service", on_delete=models.SET_NULL, null=True, blank=True, related_name="radius_actions"
    )
    username = models.CharField(max_length=255, db_index=True)
    action = models.CharField(max_length=20, choices=Action.choices)
    transport = models.CharField(max_length=10, choices=Transport.choices)
    ok = models.BooleanField()
    detail = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.username}: {self.action} via {self.transport} ({'ok' if self.ok else 'FAILED'})"


class UsageBucket(models.Model):
    """How many bytes one RADIUS login moved in one clock hour.

    Why this table has to exist at all: radacct holds a CUMULATIVE counter per
    session, with no timeline behind it. A PPPoE session running from the 3rd
    to the 20th is one row, stamped with its start time, holding seventeen
    days of traffic. Asking "what did they use on the 11th" of that data is
    unanswerable -- the old monthly figure sidestepped it by attributing a
    whole session to the month it STARTED in, which is tolerable across a
    month and useless across a day: a daily chart built that way shows one
    enormous spike on the connection day and zeros either side.

    So usage is accumulated instead of derived. sample_session_usage already
    runs every minute and already tracks each session's previous counters in
    order to compute a rate; the delta it works out is banked here, into the
    hour it was observed. That makes the attribution correct by construction
    -- bytes land in the hour they were actually reported.

    Hourly rather than daily because it costs the same to write and answers
    more: a day view becomes 24 bars, a week and a month roll up to days, a
    year to months. One table, four views.

    The trade-off, stated plainly: figures only exist from the moment this
    started running. There is no way to reconstruct earlier hours from
    accounting data, and the platform does not pretend otherwise.
    """

    username = models.CharField(max_length=255, db_index=True)
    # Always the exact top of an hour, in UTC. Stored rather than derived so
    # the unique constraint below can do the de-duplication, which is what
    # makes the sampler safe to run twice in the same minute.
    bucket_start = models.DateTimeField(db_index=True)
    # From the CUSTOMER's point of view, which is the reverse of RADIUS's:
    # the NAS reports acctinputoctets as traffic coming IN to itself, i.e.
    # the customer's upload. Getting this backwards is invisible until
    # somebody notices their download figure tracks their upload.
    download_bytes = models.BigIntegerField(default=0)
    upload_bytes = models.BigIntegerField(default=0)

    class Meta:
        ordering = ["bucket_start"]
        constraints = [
            models.UniqueConstraint(fields=["username", "bucket_start"], name="usage_bucket_unique_hour"),
        ]
        indexes = [
            # The shape every query here uses: one login (or a few), a date
            # range, in order.
            models.Index(fields=["username", "bucket_start"], name="usage_bucket_user_time"),
        ]

    def __str__(self):
        return f"{self.username} {self.bucket_start:%Y-%m-%d %H:00}: {self.download_bytes}d/{self.upload_bytes}u"


class LiveTrafficInterest(models.Model):
    """Is anyone actually looking at this router's live figures right now?

    The whole point of the on-demand design. The previous arrangement polled
    every router every minute, around the clock, for a number that is only
    ever read live by a human currently on the page -- roughly 1,440 logins a
    day per router, almost all of them for nobody. A router's own log made
    that obvious: our logins every minute, forever, against a comparable
    platform's one short connection an hour.

    So a viewer now says "I am watching" on each poll, and the background
    reader keeps the router connection open only while that stays fresh. When
    the last staff member navigates away it stops within seconds and the
    router sees nothing from us at all.

    One row per device, updated in place -- this is a flag with a timestamp,
    not a history.
    """

    device = models.OneToOneField(
        "network.Device", on_delete=models.CASCADE, related_name="live_interest"
    )
    last_requested_at = models.DateTimeField(db_index=True)

    class Meta:
        verbose_name_plural = "live traffic interest"

    def __str__(self):
        return f"{self.device}: last wanted at {self.last_requested_at:%H:%M:%S}"


class SpeedWindow(models.Model):
    """A time of day when lines run at a different speed.

    The point is load, not generosity: a wireless network's capacity is
    finite and its evenings are full while its small hours are empty.
    Handing customers more speed at 02:00 costs nothing that is being
    used, and it moves the big downloads -- console updates, backups,
    long streams -- off the hours when everyone else is trying to work.

    Percent of the plan rather than an absolute speed, so one window
    covers every tariff. 200 doubles a 10 Mbps line and doubles a 50 Mbps
    line, and neither needs its own row.
    """

    name = models.CharField(max_length=100, help_text='e.g. "Night burst", "Sunday afternoon".')
    # Null means it applies to every tariff. Most networks want one
    # network-wide window, and making them attach it to each plan by hand
    # is how a new plan silently ends up outside the schedule.
    tariff = models.ForeignKey(
        "billing.Tariff",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="speed_windows",
        help_text="Leave blank to apply to every tariff.",
    )
    start_time = models.TimeField()
    end_time = models.TimeField(
        help_text="May be earlier than the start, for a window that runs through midnight."
    )
    # Mon=0 .. Sun=6, matching Python's weekday(). Empty means every day,
    # the same empty-means-everything convention used for allowed_sections
    # and visible_partners.
    weekdays = ArrayField(
        models.PositiveSmallIntegerField(),
        blank=True,
        default=list,
        help_text="Days it applies to (0=Mon … 6=Sun). Empty = every day.",
    )
    speed_pct = models.PositiveIntegerField(
        default=200,
        help_text="Percent of the plan speed while this window is on. 200 = double, 100 = no change.",
    )
    # Whether traffic moved inside this window counts toward the fair-use
    # total. Off by default, because a window that still counts gives
    # nobody any reason to move their downloads into it -- which is the
    # entire purpose of having one.
    counts_toward_fup = models.BooleanField(
        default=False,
        help_text="Off (recommended): traffic in this window doesn't count toward fair use.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_time", "name"]

    def __str__(self):
        return f"{self.name} ({self.start_time:%H:%M}–{self.end_time:%H:%M}, {self.speed_pct}%)"

    def covers(self, when):
        """Whether this window is on at `when` (a local datetime).

        Handles a window that runs THROUGH MIDNIGHT, which is the shape
        almost every off-peak window actually has. 22:00–06:00 is not the
        empty set, and a naive start <= t <= end test makes it one -- the
        window would simply never fire and nobody would know why.
        """
        if not self.is_active:
            return False
        t = when.time()
        if self.start_time <= self.end_time:
            in_span = self.start_time <= t < self.end_time
            day = when.weekday()
        else:
            # Runs past midnight. After the start it belongs to today;
            # before the end it belongs to the day it STARTED on, which is
            # what a "Friday night" window has to mean when it is 01:00 on
            # Saturday.
            if t >= self.start_time:
                in_span, day = True, when.weekday()
            elif t < self.end_time:
                in_span, day = True, (when.weekday() - 1) % 7
            else:
                in_span, day = False, when.weekday()
        if not in_span:
            return False
        return not self.weekdays or day in self.weekdays
