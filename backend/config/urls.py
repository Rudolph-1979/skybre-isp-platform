from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("accounts.urls")),
    path("api/", include("customers.urls")),
    path("api/", include("billing.urls")),
    path("api/", include("network.urls")),
    path("api/", include("tickets.urls")),
    path("api/", include("scheduling.urls")),
]
