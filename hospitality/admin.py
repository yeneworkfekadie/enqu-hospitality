from django.contrib import admin

from .models import Guest, Hotel, Reservation, Room, RoomType


@admin.register(Hotel)
class HotelAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "city", "default_currency", "is_active")
    list_filter = ("city", "default_currency", "is_active")
    search_fields = ("name", "code", "city")
    prepopulated_fields = {"code": ("name",)}


@admin.register(RoomType)
class RoomTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "hotel", "max_occupancy", "base_rate", "rate_currency")
    list_filter = ("hotel", "rate_currency")
    search_fields = ("name", "hotel__name")


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("room_number", "hotel", "room_type", "floor", "status")
    list_filter = ("hotel", "status", "room_type")
    search_fields = ("room_number", "hotel__name")


@admin.register(Guest)
class GuestAdmin(admin.ModelAdmin):
    list_display = ("first_name", "last_name", "email", "phone", "nationality")
    search_fields = (
        "first_name",
        "last_name",
        "email",
        "phone",
        "document_number",
    )


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ("guest", "room", "check_in", "check_out", "status")
    list_filter = ("status", "room__hotel", "check_in")
    search_fields = (
        "guest__first_name",
        "guest__last_name",
        "room__room_number",
        "room__hotel__name",
    )
    date_hierarchy = "check_in"
