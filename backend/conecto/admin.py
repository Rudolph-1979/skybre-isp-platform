from django.contrib import admin

from .models import (
    ConectoBandwidthSession, ConectoCall, ConectoDocumentToken, ConectoInternalTicket,
    ConectoInvoice, ConectoJob, ConectoJobEvent, ConectoLink, ConectoStockChecklist,
    ConectoTechnician, ConectoTechnicianPosition, ConectoTicket, ConectoUsagePush,
)


@admin.register(ConectoTicket)
class ConectoTicketAdmin(admin.ModelAdmin):
    list_display = ("ticket", "conecto_ticket_id", "origin", "created_at")
    list_filter = ("origin",)
    search_fields = ("conecto_ticket_id", "ticket__ticket_number")
    raw_id_fields = ("ticket",)


@admin.register(ConectoLink)
class ConectoLinkAdmin(admin.ModelAdmin):
    list_display = ("customer", "firebase_uid", "email", "registered_at")
    search_fields = ("firebase_uid", "email", "customer__customer_id", "customer__full_name")
    raw_id_fields = ("customer",)


@admin.register(ConectoCall)
class ConectoCallAdmin(admin.ModelAdmin):
    list_display = (
        "endpoint", "status", "http_status", "subject_kind", "subject_ref",
        "attempts", "created_at",
    )
    list_filter = ("status", "endpoint")
    search_fields = ("subject_ref", "detail")
    # Read-only throughout: this is an audit trail. Editing a row here
    # would change the record of what was sent without changing what was
    # sent, which makes the table worse than not having it.
    readonly_fields = tuple(f.name for f in ConectoCall._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(ConectoTechnician)
class ConectoTechnicianAdmin(admin.ModelAdmin):
    list_display = ("technician_code", "user", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("technician_code", "user__username", "user__first_name")
    raw_id_fields = ("user",)


@admin.register(ConectoJob)
class ConectoJobAdmin(admin.ModelAdmin):
    # One row per (job, technician) now, so both are shown -- a job with
    # two people on it is two rows here, one each, rather than one row
    # that used to be the whole story.
    list_display = ("job", "technician", "technician_code", "conecto_job_id", "last_status", "pushed_at")
    list_filter = ("last_status",)
    search_fields = ("technician_code", "conecto_job_id")
    raw_id_fields = ("job", "technician")


@admin.register(ConectoJobEvent)
class ConectoJobEventAdmin(admin.ModelAdmin):
    list_display = (
        "received_at", "technician_code", "status", "vexar_job_id",
        "km_traveled", "duration_minutes",
    )
    list_filter = ("status", "technician_code")
    search_fields = ("vexar_job_id", "conecto_job_id", "technician_code")
    raw_id_fields = ("job",)
    # The trail a future "where were they" question gets answered from.
    # Editing one would change the record of where somebody was.
    readonly_fields = tuple(f.name for f in ConectoJobEvent._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(ConectoStockChecklist)
class ConectoStockChecklistAdmin(admin.ModelAdmin):
    list_display = (
        "effective_date", "technician_code", "any_missing", "completed_at", "sent_at",
    )
    list_filter = ("any_missing", "effective_date")
    search_fields = ("technician_code",)


@admin.register(ConectoTechnicianPosition)
class ConectoTechnicianPositionAdmin(admin.ModelAdmin):
    list_display = ("technician_code", "recorded_at", "lat", "lng", "job", "received_at")
    list_filter = ("technician_code",)
    search_fields = ("technician_code", "vexar_job_id", "conecto_job_id")
    raw_id_fields = ("job",)
    date_hierarchy = "recorded_at"
    # Read-only, like the event log above it. These rows are where "where
    # was he at half past two" is answered from, and an editable answer to
    # that question is not an answer.
    readonly_fields = tuple(f.name for f in ConectoTechnicianPosition._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(ConectoInternalTicket)
class ConectoInternalTicketAdmin(admin.ModelAdmin):
    list_display = ("ticket", "category", "technician_code", "job", "created_at")
    list_filter = ("category",)
    search_fields = ("conecto_ticket_id", "ticket__ticket_number", "technician_code")
    raw_id_fields = ("ticket", "job")


@admin.register(ConectoBandwidthSession)
class ConectoBandwidthSessionAdmin(admin.ModelAdmin):
    list_display = (
        "customer", "started_at", "expires_at", "stopped_at", "poll_count",
    )
    search_fields = ("customer__customer_id", "customer__full_name", "session_id")
    raw_id_fields = ("customer",)
    date_hierarchy = "started_at"
    readonly_fields = tuple(f.name for f in ConectoBandwidthSession._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(ConectoInvoice)
class ConectoInvoiceAdmin(admin.ModelAdmin):
    list_display = ("pushed_number", "pushed_status", "pushed_at", "invoice")
    list_filter = ("pushed_status",)
    search_fields = ("pushed_number", "invoice__number")
    raw_id_fields = ("invoice",)


@admin.register(ConectoDocumentToken)
class ConectoDocumentTokenAdmin(admin.ModelAdmin):
    list_display = ("kind", "customer", "period", "created_at", "expires_at", "fetched_at")
    list_filter = ("kind",)
    search_fields = ("token", "customer__customer_id", "invoice__number")
    raw_id_fields = ("customer", "invoice")
    # Deleting a row here REVOKES the link, which is the whole reason these
    # are stored tokens rather than signatures. Adding one by hand is not a
    # thing anybody should do: mint them through the endpoint.
    readonly_fields = ("token", "created_at", "fetched_at")

    def has_add_permission(self, request):
        return False


@admin.register(ConectoUsagePush)
class ConectoUsagePushAdmin(admin.ModelAdmin):
    list_display = (
        "customer", "period", "download_bytes", "upload_bytes", "pushed_at",
    )
    list_filter = ("period",)
    search_fields = ("customer__customer_id", "customer__full_name", "period")
    raw_id_fields = ("customer",)
    # Editable, unlike the audit tables above it: clearing pushed_digest by
    # hand is the supported way to force one customer's next run to re-send
    # without --resend re-sending for everybody.
