from django.conf import settings
from django.contrib import admin
from django.urls import path, include
from django.views.static import serve as static_serve

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("accounts.urls")),
    path("api/", include("customers.urls")),
    path("api/", include("billing.urls")),
    path("api/", include("network.urls")),
    path("api/", include("tickets.urls")),
    path("api/", include("scheduling.urls")),
    path("api/", include("inventory.urls")),
    path("api/", include("notifications.urls")),
    # Serves supplier-invoice attachments etc. Auth is enforced by the
    # API when listing/creating receipts; this is just the raw file byte
    # stream, same trust level as any other file behind this VPS's IP.
    path("media/<path:path>", static_serve, {"document_root": settings.MEDIA_ROOT}),
]
