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
