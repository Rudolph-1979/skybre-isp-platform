"""TOTP two-factor auth helpers — kept separate from models.py/views.py
so the pyotp/qrcode dependency surface is in one obvious place."""

import base64
import io
import secrets
import string

import pyotp
import qrcode

from .models import TwoFactorBackupCode

ISSUER_NAME = "Skybre ISP Platform"
BACKUP_CODE_COUNT = 10


def generate_secret():
    return pyotp.random_base32()


def provisioning_uri(secret, username):
    return pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER_NAME)


def qr_code_data_uri(secret, username):
    uri = provisioning_uri(secret, username)
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def verify_totp_code(secret, code):
    if not code:
        return False
    # valid_window=1 tolerates ~30s of clock drift between server and phone.
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)


def generate_backup_codes(device):
    """Creates BACKUP_CODE_COUNT fresh codes for `device`, deleting any
    existing ones first. Returns the plaintext codes — the only time
    they're ever available in plaintext, so the caller must show them to
    the user immediately."""

    device.backup_codes.all().delete()
    alphabet = string.ascii_uppercase + string.digits
    plain_codes = []
    for _ in range(BACKUP_CODE_COUNT):
        raw = "".join(secrets.choice(alphabet) for _ in range(8))
        formatted = f"{raw[:4]}-{raw[4:]}"
        plain_codes.append(formatted)
        code = TwoFactorBackupCode(device=device)
        code.set_code(formatted)
        code.save()
    return plain_codes


def verify_and_consume_backup_code(device, code):
    if not code:
        return False
    for backup_code in device.backup_codes.filter(used=False):
        if backup_code.check_code(code.strip()):
            from django.utils import timezone

            backup_code.used = True
            backup_code.used_at = timezone.now()
            backup_code.save(update_fields=["used", "used_at"])
            return True
    return False


def verify_code(device, code):
    """Accepts either a live TOTP code or an unused backup code."""
    return verify_totp_code(device.secret, code) or verify_and_consume_backup_code(device, code)
