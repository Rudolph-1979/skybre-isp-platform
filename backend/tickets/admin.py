from django.contrib import admin
from .models import Ticket, TicketComment


class TicketCommentInline(admin.TabularInline):
    model = TicketComment
    extra = 0


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("ticket_number", "subject", "customer", "status", "priority", "assigned_to", "created_at")
    list_filter = ("status", "priority", "department")
    inlines = [TicketCommentInline]
    # `status` is read-only here, not just gated by role, for a second
    # reason beyond the Resolved/Closed permission rules TicketViewSet.
    # perform_update enforces (only the customer resolves, only Management/
    # Admin closes): that same method is the ONLY place `resolved_at` gets
    # stamped and `disqualified_from_kpi` gets set on a reopen (see
    # Ticket.resolved_at's own comment). An admin-panel edit bypasses the
    # ModelViewSet entirely -- it calls the model form's save(), never
    # perform_update -- so flipping status here wouldn't just dodge the
    # permission check, it would silently leave `resolved_at` unstamped (or
    # stale) and never disqualify a reopened ticket from the yearly KPI
    # report, corrupting the bonus numbers with no error and no trace.
    # Status changes belong on the one path that keeps both correct.
    readonly_fields = ("status",)
