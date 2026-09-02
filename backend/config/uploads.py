"""Shared validation for staff-uploaded file attachments.

Three models let staff attach a file: expenses.Expense (receipts),
inventory.StockReceipt (supplier invoices) and payroll.LeaveRequest (sick
notes). None of them validated anything, which mattered more than it
looks: uploads are served back from /media/ on the *same origin* as the
SPA, and django.views.static.serve derives Content-Type from the file
extension. So a file called `receipt.html` (or `.svg`, which browsers
also execute script in) came back as text/html and ran in the app's
origin -- where the JWT lives in localStorage. Any staff member who can
file a leave request could hand an admin a token-stealing page.

config.urls.protected_media_serve now forces a download-only response,
which is the real fix; this allowlist is the second layer, so a dangerous
file can't be stored in the first place. Keep the two in step.
"""
from django.core.validators import FileExtensionValidator
from django.core.exceptions import ValidationError

# Receipts, invoices and sick notes are photographed or scanned in
# practice -- documents and images only. Deliberately no .svg (scriptable
# XML), no .html/.htm/.xhtml, no .js, and no archive/office-macro formats.
ALLOWED_ATTACHMENT_EXTENSIONS = ["pdf", "png", "jpg", "jpeg", "webp", "heic", "gif", "txt"]

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024  # 15 MB


def validate_attachment_size(value):
    if value and value.size > MAX_ATTACHMENT_BYTES:
        raise ValidationError(
            f"That file is {value.size / 1024 / 1024:.1f} MB — the limit is "
            f"{MAX_ATTACHMENT_BYTES // 1024 // 1024} MB. Please compress it or upload a smaller scan."
        )


ATTACHMENT_VALIDATORS = [
    FileExtensionValidator(allowed_extensions=ALLOWED_ATTACHMENT_EXTENSIONS),
    validate_attachment_size,
]
