"""Source blocks inserted by apply_conecto_replies.py."""

# ---------------------------------------------------------------------------
# conecto/client.py
# ---------------------------------------------------------------------------

DEFAULT_URL = '''    "addTicketReply": "https://africa-south1-skybre-connecto.cloudfunctions.net/addTicketReply",
'''

ADD_REPLY = '''def add_ticket_reply(conecto_ticket_id, message, *, author_name=None, subject_ref=""):
    """Append a reply to a Conecto ticket, visible to the client.

    THE ID GOES IN THE URL PATH, like updateTicket -- the stored URL is
    the bare function and the id is appended here, so a configured value
    carrying a literal "{ticketId}" would be wrong.

    TWO THINGS CONECTO TOLD US THAT SHAPE THIS.

    There is no internal-note mode. Everything sent here lands in the
    same thread the client reads and fires a push notification to their
    phone. So the decision about WHAT may be sent cannot live here; it
    lives at the signal, which refuses internal notes outright.

    The author is a label, never a person. Conecto's own app shows staff
    replies as "Support Team" rather than a name, and sending a real one
    would leak our staff's identities to customers through a channel
    nobody chose that for. CONECTO_REPLY_AUTHOR_NAME overrides the label
    if the wording should change; it is not a place to put a username.
    """
    message = (message or "").strip()
    if not message:
        # Their API rejects an empty message with a 400, and a blank reply
        # would notify the client about nothing at all.
        return None

    base = _url("addTicketReply").rstrip("/")
    if not base:
        return None

    payload = {
        "message": message,
        "author_name": author_name or getattr(
            settings, "CONECTO_REPLY_AUTHOR_NAME", "Support Team"
        ),
    }
    return call(
        "addTicketReply", payload,
        url=f"{base}/{conecto_ticket_id}",
        subject_kind="ticket", subject_ref=subject_ref or conecto_ticket_id,
    )


'''

# ---------------------------------------------------------------------------
# conecto/signals.py
# ---------------------------------------------------------------------------

SIGNALS_IMPORT = '''from tickets.models import TicketComment
'''

COMMENT_PUSH = '''@receiver(post_save, sender=TicketComment, dispatch_uid="conecto_comment_push")
def _push_ticket_comment(sender, instance, created, **kwargs):
    """Send a staff reply to the client, and nothing else.

    EVERY GUARD HERE EXISTS TO STOP ONE THING REACHING A CUSTOMER'S
    PHONE.

    Conecto has no internal-note mode: whatever we send appears in the
    thread the client reads and pushes a notification to them. An
    internal note is, by definition, the one thing on a ticket that was
    written on the understanding the customer would never see it --
    handover context, a credit decision, an opinion about the customer
    themselves. Sending one is not a bug that can be corrected
    afterwards; the notification has already arrived.

    So the refusals are deliberately redundant:

      * is_internal, the field that means exactly this;
      * external_author, set only on comments that CAME FROM the client,
        which must not be echoed back to them as if we had written them;
      * is_suppressed(), the thread-local the inbound webhook wraps its
        writes in, which is the same guard the ticket push uses;
      * created, because an edit is not a new reply, and pushing on every
        save would re-send the whole thread each time somebody fixed a
        typo.

    Any one of these would usually be enough. They are all here because
    the cost of them all failing at once is a private note on a
    customer's phone, and the cost of one being redundant is nothing.
    """
    if is_suppressed() or not created:
        return
    if instance.is_internal or instance.external_author:
        return
    transaction.on_commit(lambda: _send_ticket_comment(instance.pk))


def _send_ticket_comment(comment_pk):
    """Re-read and re-check before sending.

    Re-read for the same reason _create_ticket does: on_commit runs after
    the transaction, and the in-memory object may not be what landed.
    Re-check is_internal because the gap between save and commit is a
    window, and this is the one decision worth paying a second query to
    be sure of.
    """
    comment = (
        TicketComment.objects.select_related("ticket")
        .filter(pk=comment_pk)
        .first()
    )
    if comment is None or comment.is_internal or comment.external_author:
        return

    link = ConectoTicket.objects.filter(ticket_id=comment.ticket_id).first()
    if link is None:
        # Not a ticket Conecto knows about -- raised in Skybre for a
        # customer who does not use the app, or a push that failed and is
        # waiting for conecto_resync. Either way there is no thread there
        # to reply into.
        return

    client.add_ticket_reply(
        link.conecto_ticket_id,
        comment.message,
        subject_ref=comment.ticket.ticket_number,
    )


'''

# ---------------------------------------------------------------------------
# conecto/views.py
# ---------------------------------------------------------------------------

VIEWS_IMPORT = '''from tickets.models import TicketComment
'''

REPLIED_WEBHOOK = '''class TicketRepliedWebhook(ConectoWebhook):
    """A subscriber replied to their ticket in the app.

    The counterpart to client.add_ticket_reply. Without this the
    integration is worse than nothing: staff answer into a thread they
    believe is two-way, the customer answers back, and nobody at Skybre
    ever sees it.

    The reply is stored as an ordinary ticket comment with
    external_author set to the name Conecto gives us. That field is what
    distinguishes it from a staff comment on screen -- author stays null
    because the customer is not a user of this system, and a comment with
    no name against it would read as if the platform had written it.

    is_internal is False and is never taken from the payload. Whether
    something is an internal note is our decision about our own staff's
    writing; an inbound field that could set it would let the app mark a
    customer's own message hidden from the customer.
    """

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        conecto_id = str(self._first(data, "ticket_id", "ticketId", "conecto_ticket_id") or "")
        vexar_ref = str(self._first(data, "vexar_ticket_id", "ticket_number") or "")
        message = str(self._first(data, "message", "text", "body", "reply") or "").strip()
        author = str(self._first(data, "author_name", "authorName") or "").strip()[:150]

        if not message:
            return Response(
                {"detail": "message is required."}, status=status.HTTP_400_BAD_REQUEST
            )

        link = None
        if conecto_id:
            link = ConectoTicket.objects.select_related("ticket").filter(
                conecto_ticket_id=conecto_id
            ).first()
        ticket = link.ticket if link else None
        if ticket is None and vexar_ref:
            # The other handle they hold -- our own number, which is what
            # they have for tickets we pushed to them.
            ticket = Ticket.objects.filter(ticket_number=vexar_ref).first()

        if ticket is None:
            return Response(
                {"detail": "Unknown ticket."}, status=status.HTTP_404_NOT_FOUND
            )

        # A replay answers as though it worked. Conecto logs and drops a
        # failure rather than retrying, so a 400 here would achieve
        # nothing except losing the reply -- and the same message twice in
        # a row from the same person is far more likely to be a duplicate
        # delivery than someone repeating themselves verbatim.
        last = ticket.comments.order_by("-created_at").first()
        if last is not None and last.message == message and last.external_author == (author or "Customer"):
            return Response({"ticket_number": ticket.ticket_number, "duplicate": True})

        with suppressed():
            TicketComment.objects.create(
                ticket=ticket,
                author=None,
                message=message,
                is_internal=False,
                # Named even when they send us nothing, because a reply
                # with no author beside it reads as though Skybre wrote
                # it, and staff would answer accordingly.
                external_author=author or "Customer",
            )

        # Status is deliberately untouched. A customer replying to a
        # closed ticket does not reopen it here: TicketClosedWebhook
        # exists precisely so the app cannot move a ticket to any state it
        # likes, and reopening through a side door would undo that. Worth
        # revisiting as a product decision -- a reply on a closed ticket
        # should probably surface somewhere -- but not as a silent
        # side-effect of this endpoint.
        return Response(
            {"ticket_number": ticket.ticket_number}, status=status.HTTP_201_CREATED
        )


'''

# ---------------------------------------------------------------------------
# conecto/urls.py
# ---------------------------------------------------------------------------

URLS_DOC = '''    /api/webhooks/conecto/ticket-replied/
'''

URLS_IMPORT = '''    TicketRepliedWebhook,
'''

URLS_PATH = '''    path("webhooks/conecto/ticket-replied/", TicketRepliedWebhook.as_view()),
'''

# ---------------------------------------------------------------------------
# tickets/models.py
# ---------------------------------------------------------------------------

EXTERNAL_AUTHOR = '''    # Who wrote this, when it was not one of our users.
    #
    # A reply arriving from the Conecto app is written by the customer,
    # who has no account here, so `author` is null. Null alone is
    # indistinguishable from a comment the platform generated, and a
    # customer's message shown with no name against it reads as ours.
    #
    # Blank on every comment written by staff -- `author` covers those.
    external_author = models.CharField(max_length=150, blank=True)
'''

# ---------------------------------------------------------------------------
# tickets/serializers.py  (replacements)
# ---------------------------------------------------------------------------

SERIALIZER_AUTHOR_OLD = '''    author_name = serializers.CharField(source="author.username", read_only=True, default=None)
'''

SERIALIZER_AUTHOR_NEW = '''    # One field for "who said this", whoever they are. The screen already
    # renders author_name, so a customer's reply arriving through the
    # Conecto app shows their name there without the frontend changing --
    # and external_author is exposed alongside it so the UI can mark those
    # differently when somebody gets to it.
    author_name = serializers.SerializerMethodField()

    def get_author_name(self, obj):
        if obj.external_author:
            return obj.external_author
        return obj.author.username if obj.author_id else None
'''

SERIALIZER_FIELDS_OLD = '''        fields = ["id", "ticket", "author", "author_name", "message", "is_internal", "created_at"]
'''

SERIALIZER_FIELDS_NEW = '''        fields = [
            "id", "ticket", "author", "author_name", "external_author",
            "message", "is_internal", "created_at",
        ]
'''

SERIALIZER_READONLY_OLD = '''        read_only_fields = ["id", "created_at", "author"]
'''

SERIALIZER_READONLY_NEW = '''        # external_author is read-only through the API on purpose: it is set
        # by the Conecto webhook and nowhere else. A staff member able to
        # write it could post a comment under a customer's name.
        read_only_fields = ["id", "created_at", "author", "external_author"]
'''

# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------

MIGRATION = '''"""Name the author of a ticket comment that came from outside.

A reply from the Conecto app is written by the customer, who has no user
account here, so TicketComment.author is null for it. Null is already how
a platform-generated comment looks, so without this field a customer's
own words appear on the ticket with nothing to say they are the
customer's.

Blank, not null: every existing comment was written by a staff user and
"no external author" is a real answer for them, not a missing one.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "%(head)s"),
    ]

    operations = [
        migrations.AddField(
            model_name="ticketcomment",
            name="external_author",
            field=models.CharField(blank=True, max_length=150),
        ),
    ]
'''
