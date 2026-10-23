from django.contrib import admin

from .models import FuelLog, OdometerReading, ServiceRecord, Vehicle


class OdometerReadingInline(admin.TabularInline):
    model = OdometerReading
    extra = 0


class ServiceRecordInline(admin.TabularInline):
    model = ServiceRecord
    extra = 0


class FuelLogInline(admin.TabularInline):
    model = FuelLog
    extra = 0


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("registration_number", "make", "model", "year", "fuel_type", "assigned_to", "is_active")
    list_filter = ("is_active", "make", "fuel_type")
    search_fields = ("make", "model", "registration_number")
    inlines = [OdometerReadingInline, ServiceRecordInline, FuelLogInline]
