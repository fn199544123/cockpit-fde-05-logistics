from django.contrib import admin

from .models import Order, Wave, PickTask, Vehicle, Route


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'driver_alias', 'capacity', 'status')
    list_filter = ('status',)
    search_fields = ('code', 'name', 'driver_alias')


@admin.register(Wave)
class WaveAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'zone', 'status', 'planned_start', 'planned_end')
    list_filter = ('status', 'zone')
    search_fields = ('code', 'name')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('code', 'receiver', 'item_count', 'status', 'wave')
    list_filter = ('status',)
    search_fields = ('code', 'receiver', 'address')


@admin.register(PickTask)
class PickTaskAdmin(admin.ModelAdmin):
    list_display = ('code', 'wave', 'order', 'location', 'picker_alias', 'qty', 'status')
    list_filter = ('status',)
    search_fields = ('code', 'location', 'picker_alias')


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'vehicle', 'stops', 'status', 'depart_at')
    list_filter = ('status',)
    search_fields = ('code', 'name')
    filter_horizontal = ('orders',)
