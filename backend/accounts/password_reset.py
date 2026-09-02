"""Password reset email helper -- shared by the public "forgot password"
flow (PasswordResetRequestView) and the admin-triggered "send reset link"
action on StaffAccountsViewSet, so both paths build/send the exact same
link the exact same way.

Uses Django's built-in PasswordResetTokenGenerator: a signed, stateless
token derived from the user's pk, password hash, and last_login, so it
needs no extra database table and automatically stops working the moment
the password changes, the user logs in, or PASSWORD_RESET_TIMEOUT (Django
default: 3 days) elapses -- whichever comes first.
"""

from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMessage
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from notifications.email_settings import get_email_config, get_email_connection


def send_password_reset_email(user) -> None:
    cfg = get_email_config()
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    reset_url = f"{cfg['site_url'].rstrip('/')}/reset-password/{uid}/{token}/"

    greeting_name = user.first_name or user.username
    message = EmailMessage(
        subject=f"Reset your {cfg['company_name']} password",
        body=(
            f"Hi {greeting_name},\n\n"
            f"Someone requested a password reset for your {cfg['company_name']} account "
            f"({user.username}). If this was you, set a new password here:\n\n"
            f"{reset_url}\n\n"
            "This link can only be used once and expires in a few days.\n\n"
            "If you didn't request this, you can safely ignore this email -- your password "
            "hasn't been changed."
        ),
        from_email=cfg["from_email"],
        to=[user.email],
        connection=get_email_connection(),
    )
    message.send(fail_silently=False)


def send_staff_invite_email(user, invited_by=None) -> None:
    """Invite a newly created staff member to set their own password.

    Same signed, stateless token as a password reset -- deliberately, because
    the two are the same act from the token's point of view: prove you hold
    this mailbox, then choose a password. Reusing it means there is one link
    format, one expiry rule, and one place for either to go wrong.

    Why this exists: creating an account used to require the admin to invent a
    password and then get it to the person somehow -- read out over the phone,
    sent in a chat message, written on a note. Every one of those is a
    password sitting somewhere it shouldn't, chosen by the wrong person. The
    invite means the account is created with no usable password at all and the
    only way in is through the mailbox we sent it to.
    """
    cfg = get_email_config()
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    # The same URL the reset flow uses, so there is one page to maintain.
    invite_url = f"{cfg['site_url'].rstrip('/')}/reset-password/{uid}/{token}/"

    greeting_name = user.first_name or user.username
    inviter = ""
    if invited_by is not None:
        who = (invited_by.get_full_name() or invited_by.username).strip()
        if who:
            inviter = f"{who} has set up an account for you"
    if not inviter:
        inviter = "An account has been set up for you"

    message = EmailMessage(
        subject=f"Your {cfg['company_name']} account",
        body=(
            f"Hi {greeting_name},\n\n"
            f"{inviter} on {cfg['company_name']}.\n\n"
            f"Your username is: {user.username}\n\n"
            "Choose your password here to get started:\n\n"
            f"{invite_url}\n\n"
            "This link can only be used once and expires in a few days. If it has expired by "
            "the time you get to it, use the 'Forgot password' link on the sign-in page and "
            "a fresh one will be sent.\n\n"
            "If you weren't expecting this, you can ignore it -- the account can't be used "
            "until a password is set."
        ),
        from_email=cfg["from_email"],
        to=[user.email],
        connection=get_email_connection(),
    )
    message.send(fail_silently=False)
