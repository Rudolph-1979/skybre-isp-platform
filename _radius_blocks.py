"""Source blocks inserted by apply_radius_guards.py.

Kept out of the patcher so the code being inserted can be read as code
rather than as string literals inside an edit list.
"""

# ---------------------------------------------------------------------------
# clients_conf.py
# ---------------------------------------------------------------------------

CLIENTS_CONF_IMPORTS = '''import datetime
import json
'''

COUNT_HELPER = '''def count_client_blocks(text):
    """How many `client "..." {` blocks a rendered config contains.

    This does not parse FreeRADIUS config and does not need to. The only
    distinction that has ever mattered operationally is "has clients"
    versus "has none" -- a config with none is perfectly valid, starts
    cleanly, reports itself healthy, and ignores every NAS on the network.
    """
    return sum(1 for line in text.splitlines() if line.startswith("client "))


'''

SPOOL_GUARD = '''
        # A render with no clients in it is not a config, it is an outage.
        #
        # 2026-09-20: this function rendered an empty config. Every NAS row
        # was in the database and active the whole time -- why the query
        # came back empty was never established -- and the host installed
        # the result because an empty clients.conf validates perfectly.
        # FreeRADIUS then ignored every router on the network for ten
        # hours while the journal reported a healthy restart.
        #
        # The check deliberately compares against the FILE rather than
        # re-running the query. Whatever made the query return nothing
        # would make a second query return nothing too; what is already on
        # disk is independent evidence that clients existed a moment ago.
        if count_client_blocks(rendered) == 0 and not getattr(
            settings, "RADIUS_ALLOW_EMPTY_CLIENTS", False
        ):
            try:
                with open(path) as existing:
                    live = count_client_blocks(existing.read())
            except OSError:
                live = 0
            if live:
                logger.error(
                    "REFUSING to spool a clients.conf with no clients over one "
                    "holding %s. Rendering found no active RadiusNasClient rows, "
                    "but %s client(s) are live on the RADIUS server, so writing "
                    "this would take every NAS off the network. The spool file "
                    "is unchanged. Reason given: %s",
                    live, live, reason or "no reason given",
                )
                return None
'''

STATE_FUNCTION = '''

def clients_conf_state():
    """What FreeRADIUS is actually running, as far as this side can see.

    write_clients_conf_spool's docstring has promised the frontend this
    function since the spool was built. It never existed -- which is part
    of why the 2026-09-20 outage stayed invisible for ten hours: the
    platform had no way to say "the config this screen describes is not
    the config the RADIUS server is running", so a dead network and a
    healthy one looked identical on every page.

    The host writes apply_status.json beside the spool file on every
    attempt, successful or not. Reading it here is what turns a journal
    entry nobody is watching into something a screen can show.
    """
    from .models import RadiusNasClient

    path = spool_path()
    state = {
        "spool_configured": bool(path),
        "active_clients": RadiusNasClient.objects.filter(is_active=True).count(),
        "spool_clients": None,
        "spool_written_at": None,
        "apply": None,
        "problems": [],
    }

    if not path:
        state["problems"].append(
            "RADIUS_CLIENTS_SPOOL is not configured, so RADIUS client changes "
            "made here never reach FreeRADIUS."
        )
        return state

    try:
        with open(path) as spool:
            state["spool_clients"] = count_client_blocks(spool.read())
        state["spool_written_at"] = datetime.datetime.fromtimestamp(
            os.path.getmtime(path), datetime.timezone.utc
        ).isoformat()
    except OSError:
        state["problems"].append(
            "The spool file has never been written, so no RADIUS client change "
            "has ever reached the host."
        )
        return state

    try:
        with open(os.path.join(os.path.dirname(path), "apply_status.json")) as status:
            state["apply"] = json.load(status)
    except (OSError, ValueError):
        state["problems"].append(
            "The host has not reported back on any apply attempt, so what "
            "FreeRADIUS is running right now is unknown."
        )
        return state

    applied = state["apply"] or {}
    detail = str(applied.get("detail") or "")
    live = applied.get("live_clients")

    if applied.get("state") == "outage":
        state["problems"].append(
            "FreeRADIUS is running a config with no NAS clients in it. Every "
            "router is being ignored and all customers will read as offline. "
            + detail
        )
    elif applied.get("state") == "refused":
        state["problems"].append(
            "The host refused the last config because it contained no clients. "
            "FreeRADIUS is still running the previous, working one. " + detail
        )
    elif applied.get("state") == "failed":
        state["problems"].append(
            "The last attempt to apply RADIUS clients failed: " + detail
        )
    elif (
        applied.get("state") == "ok"
        and live is not None
        and state["spool_clients"] is not None
        and live != state["spool_clients"]
    ):
        # Not an error on its own -- the watcher may simply not have run
        # yet -- but it is the difference between what staff see and what
        # the network is doing, which is the thing worth surfacing.
        state["problems"].append(
            "The host last applied %s client(s) but the current config holds "
            "%s, so a change has not been applied yet."
            % (live, state["spool_clients"])
        )

    return state
'''

# ---------------------------------------------------------------------------
# models.py
# ---------------------------------------------------------------------------

MODELS_IMPORT = '''from django.conf import settings
'''

NAS_AUDIT_FIELDS = '''
    # WHO AND WHEN.
    #
    # Twelve writes hit these rows on the evening of 2026-09-20 and not one
    # of them left a trace. The container that served the requests was
    # replaced two hours later and took its logs with it, DRF writes no
    # audit row, and the model recorded only when a row was CREATED. So
    # afterwards, "who deactivated the NAS clients" had no answer anywhere
    # in the system -- for the most load-bearing configuration on the
    # network, where an empty list means every customer goes dark.
    #
    # updated_at answers "when was this last touched" at a glance.
    # RadiusNasClientAudit below keeps one row per write, because a single
    # timestamp says nothing about the eleven writes before the last one.
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
        help_text="Who last saved this client. Null for changes made by code rather than by a person.",
    )
'''

AUDIT_MODEL = '''class RadiusNasClientAudit(models.Model):
    """One row per write to a RadiusNasClient -- who, when, and which
    fields moved.

    Exists because of the 2026-09-20 outage, where the answer to "what
    changed just before RADIUS stopped trusting every router" was
    unobtainable. A last-modified timestamp would not have helped: there
    were twelve writes that evening and only the last one would have been
    visible.

    The secret's VALUE is never stored here, only the fact that it
    changed. An audit table gets read by more people, and exported more
    casually, than the screen it audits; a shared RADIUS secret sitting in
    it would be a worse problem than the question it answers.

    Rows are kept when the client is deleted -- that is the event most
    worth being able to look up afterwards -- which is why client_pk and
    shortname are copied rather than reached through the FK.
    """

    class Action(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        DELETED = "deleted", "Deleted"

    client = models.ForeignKey(
        RadiusNasClient, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="audit_entries",
    )
    client_pk = models.IntegerField(null=True, blank=True)
    shortname = models.CharField(max_length=64, blank=True)
    action = models.CharField(max_length=10, choices=Action.choices)
    changed_fields = ArrayField(models.CharField(max_length=64), default=list, blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at"]

    def __str__(self):
        return f"{self.action} {self.shortname or self.client_pk} at {self.changed_at:%Y-%m-%d %H:%M}"


'''

# ---------------------------------------------------------------------------
# signals.py
# ---------------------------------------------------------------------------

SIGNALS_IMPORT = '''from .models import RadiusNasClientAudit
'''

AUDIT_SIGNALS = '''

# ---------------------------------------------------------------------------
# RADIUS Clients -> audit trail
# ---------------------------------------------------------------------------
# See RadiusNasClientAudit's docstring for why this exists at all.
#
# Written from signals rather than from the viewset so that a change made
# by a management command, a migration or a shell session is recorded too.
# Those are exactly the changes nobody remembers making, and on
# 2026-09-20 the inability to rule them out cost hours.

_NAS_AUDIT_FIELDS = (
    "name", "ip_address", "shortname", "secret", "realm", "description", "is_active",
)


@receiver(pre_save, sender=RadiusNasClient)
def _nas_client_capture_before(sender, instance, **kwargs):
    """Stash the pre-save values so post_save can report what moved.

    Read back from the database rather than from the instance: by the
    time post_save runs the instance holds only the new values, so the
    comparison has to be against what the row still contained a moment
    ago.
    """
    if not instance.pk:
        instance._audit_before = None
        return
    instance._audit_before = (
        sender.objects.filter(pk=instance.pk).values(*_NAS_AUDIT_FIELDS).first()
    )


def _nas_audit_actor(instance):
    """Who to record. `changed_by` is set by the API on save; `_changed_by`
    is set on the instance for deletes, where the stored changed_by names
    the last person to SAVE the row rather than the one removing it."""
    return getattr(instance, "_changed_by", None) or getattr(instance, "changed_by", None)


@receiver(post_save, sender=RadiusNasClient)
def _nas_client_audit_save(sender, instance, created, **kwargs):
    before = getattr(instance, "_audit_before", None)
    changed = []
    if not created and before is not None:
        changed = [f for f in _NAS_AUDIT_FIELDS if before.get(f) != getattr(instance, f)]
        if not changed:
            # A save that moved nothing of substance is noise in a table
            # whose whole value is being readable during an outage.
            return
    RadiusNasClientAudit.objects.create(
        client=instance,
        client_pk=instance.pk,
        shortname=instance.shortname,
        action=(
            RadiusNasClientAudit.Action.CREATED if created
            else RadiusNasClientAudit.Action.UPDATED
        ),
        changed_fields=changed,
        changed_by=_nas_audit_actor(instance),
    )


@receiver(post_delete, sender=RadiusNasClient)
def _nas_client_audit_delete(sender, instance, **kwargs):
    RadiusNasClientAudit.objects.create(
        client=None,
        client_pk=instance.pk,
        shortname=instance.shortname,
        action=RadiusNasClientAudit.Action.DELETED,
        changed_fields=[],
        changed_by=getattr(instance, "_changed_by", None),
    )
'''

# ---------------------------------------------------------------------------
# views.py
# ---------------------------------------------------------------------------

VIEWS_IMPORTS = '''from .clients_conf import clients_conf_state
from .models import RadiusNasClientAudit
from .serializers import RadiusNasClientAuditSerializer
'''

VIEWSET_ADDITIONS = '''    # The audit trail can only answer "who changed this" if the request
    # user reaches the model layer, so every write path stamps it --
    # including destroy, where the row's own changed_by holds the last
    # person to SAVE it and would name the wrong person entirely.
    def perform_create(self, serializer):
        serializer.save(changed_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(changed_by=self.request.user)

    def perform_destroy(self, instance):
        instance._changed_by = self.request.user
        instance.delete()

    @action(detail=False, methods=["get"], url_path="clients-conf-state")
    def clients_conf_state_view(self, request):
        """Whether what this screen shows is what FreeRADIUS is running.

        They can disagree, and when they do nothing else says so. The
        list on this page comes from the database; FreeRADIUS reads a
        file on the host that a watcher installs only after validating
        it. Every step between the two can fail quietly, and on
        2026-09-20 one did, for ten hours, while every screen in the
        platform looked normal.
        """
        return Response(clients_conf_state())

    @action(detail=False, methods=["get"], url_path="audit")
    def audit(self, request):
        """Recent changes to NAS clients, newest first.

        Capped rather than paginated: this is read while something is
        broken, and the useful answer is always near the top.
        """
        entries = RadiusNasClientAudit.objects.select_related("changed_by")[:200]
        return Response(RadiusNasClientAuditSerializer(entries, many=True).data)

'''

# ---------------------------------------------------------------------------
# serializers.py
# ---------------------------------------------------------------------------

SERIALIZER_IMPORT = '''from .models import RadiusNasClientAudit
'''

SERIALIZER_FIELD = '''    # Last-saved-by, for the column on Networking -> RADIUS Clients. The
    # full history sits behind RadiusNasClientAudit; this is the part that
    # belongs on the row itself, so the list answers "when did this last
    # move" without anybody opening an audit view.
    #
    # A method field rather than CharField(source="changed_by.username"):
    # the dotted source needs a `default` to survive changed_by being null,
    # and DRF skips defaults entirely on a PATCH, so the field would
    # silently vanish from exactly the responses an edit produces.
    changed_by_username = serializers.SerializerMethodField()

    def get_changed_by_username(self, obj):
        return obj.changed_by.username if obj.changed_by_id else None
'''

SERIALIZER_FIELD_NAMES = '''            "updated_at", "changed_by_username",
'''

AUDIT_SERIALIZER = '''class RadiusNasClientAuditSerializer(serializers.ModelSerializer):
    """Read-only. Note there is no `secret` here and no route by which one
    could appear -- see RadiusNasClientAudit's docstring."""

    changed_by_username = serializers.SerializerMethodField()

    def get_changed_by_username(self, obj):
        return obj.changed_by.username if obj.changed_by_id else None

    class Meta:
        model = RadiusNasClientAudit
        fields = [
            "id", "client", "client_pk", "shortname", "action",
            "changed_fields", "changed_by_username", "changed_at",
        ]


'''

# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------

MIGRATION = '''"""Audit trail and last-changed stamp for RADIUS NAS clients.

See RadiusNasClientAudit in models.py for why. Short version: the
2026-09-20 outage was caused by a change to these rows, and the platform
had no record of who made it, when, or what it was.

updated_at is nullable rather than defaulted. A default would stamp every
existing row with the date of this deploy, which reads as "all seven NAS
clients were edited on the day we added the audit trail" -- a plausible
looking lie in exactly the table people will consult after the next
outage. Null means "not known", which is the truth for rows that predate
this.
"""
import django.contrib.postgres.fields
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("radiusauth", "%(head)s"),
    ]

    operations = [
        migrations.AddField(
            model_name="radiusnasclient",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, null=True, blank=True),
        ),
        migrations.AddField(
            model_name="radiusnasclient",
            name="changed_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="+", to=settings.AUTH_USER_MODEL,
                help_text="Who last saved this client. Null for changes made by code rather than by a person.",
            ),
        ),
        migrations.CreateModel(
            name="RadiusNasClientAudit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("client_pk", models.IntegerField(blank=True, null=True)),
                ("shortname", models.CharField(blank=True, max_length=64)),
                ("action", models.CharField(choices=[("created", "Created"), ("updated", "Updated"), ("deleted", "Deleted")], max_length=10)),
                ("changed_fields", django.contrib.postgres.fields.ArrayField(
                    base_field=models.CharField(max_length=64), blank=True, default=list, size=None)),
                ("changed_at", models.DateTimeField(auto_now_add=True)),
                ("changed_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+", to=settings.AUTH_USER_MODEL)),
                ("client", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="audit_entries", to="radiusauth.radiusnasclient")),
            ],
            options={"ordering": ["-changed_at"]},
        ),
    ]
'''
