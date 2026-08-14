from django.conf import settings
from django.contrib import admin
from django.http import HttpResponseForbidden
from django.urls import path, include
from django.views.static import serve as static_serve

from config.media_security import verify_media_path


def protected_media_serve(request, path, document_root=None, show_indexes=False):
    """Only serves the file if the request carries a valid, unexpired
    signed link for this exact path (see config/media_security.py). A
    bare/guessed/bookmarked /media/... URL -- with no `sig`, a stale one,
    or one that doesn't match this path -- gets a 403, not the file."""
    if not verify_media_path(path, request.GET.get("sig", "")):
        return HttpResponseForbidden(
            "This download link is missing, invalid, or has expired. "
            "Go back to the page you downloaded it from and try again."
        )
    return static_serve(request, path, document_root=document_root, show_indexes=show_indexes)


urlpatterns = [
    # Deliberately NOT at /admin/ -- that prefix is owned by the React
    # app's entire staff panel (/admin/customers, /admin/invoices, etc.),
    # a client-side route tree. A hard refresh on any of those pages is a
    # real HTTP request that hits nginx before React ever loads, so if
    # Django's own admin sat at the same /admin/ prefix, nginx would proxy
    # every refreshed admin page straight to Django's admin site instead
    # of serving the SPA. Giving Django's admin its own path avoids that.
    path("django-admin/", admin.site.urls),
    path("api/", include("accounts.urls")),
    path("api/", include("customers.urls")),
    path("api/", include("billing.urls")),
    path("api/", include("network.urls")),
    path("api/", include("tickets.urls")),
    path("api/", include("scheduling.urls")),
    path("api/", include("inventory.urls")),
    path("api/", include("notifications.urls")),
    # Signed, short-lived download links only -- see config/media_security.py.
    # The real permission check already happened when the API response
    # that contained this link was generated (StockReceiptSerializer,
    # staff-only) -- this view just verifies that link hasn't been
    # tampered with, guessed, or reused past its expiry.
    path("media/<path:path>", protected_media_serve, {"document_root": settings.MEDIA_ROOT}),
]
