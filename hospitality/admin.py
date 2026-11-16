from django.contrib import admin
from django.contrib.auth import get_user_model

from .access import hotels_for_user
from .models import (
    Guest,
    Hotel,
    HotelStaff,
    HousekeepingTask,
    Reservation,
    Room,
    RoomType,
)


@admin.register(Hotel)
class HotelAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "city", "default_currency", "is_active")
    list_filter = ("city", "default_currency", "is_active")
    search_fields = ("name", "code", "city")
    prepopulated_fields = {"code": ("name",)}

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            pk__in=hotels_for_user(request.user)
        )

    def get_list_filter(self, request):
        if request.user.is_superuser:
            return self.list_filter
        return ("default_currency", "is_active")

    def save_model(self, request, obj, form, change):
        is_new = obj.pk is None
        super().save_model(request, obj, form, change)
        if is_new and not request.user.is_superuser:
            HotelStaff.objects.get_or_create(user=request.user, hotel=obj)


class HotelScopedAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            hotel__in=hotels_for_user(request.user)
        )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "hotel":
            kwargs["queryset"] = hotels_for_user(request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_list_filter(self, request):
        if request.user.is_superuser:
            return self.list_filter
        return tuple(
            field
            for field in self.list_filter
            if field not in {"hotel", "room_type"}
        )


@admin.register(RoomType)
class RoomTypeAdmin(HotelScopedAdmin):
    list_display = ("name", "hotel", "max_occupancy", "base_rate", "rate_currency")
    list_filter = ("hotel", "rate_currency")
    search_fields = ("name", "hotel__name")


@admin.register(Room)
class RoomAdmin(HotelScopedAdmin):
    list_display = ("room_number", "hotel", "room_type", "floor", "status")
    list_filter = ("hotel", "status", "room_type")
    search_fields = ("room_number", "hotel__name")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "room_type":
            kwargs["queryset"] = RoomType.objects.filter(
                hotel__in=hotels_for_user(request.user)
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(HotelStaff)
class HotelStaffAdmin(admin.ModelAdmin):
    list_display = ("user", "hotel")
    list_filter = ("hotel",)
    search_fields = ("user__username", "user__first_name", "user__last_name", "hotel__name")

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            hotel__in=hotels_for_user(request.user)
        )

    def get_list_filter(self, request):
        if request.user.is_superuser:
            return self.list_filter
        return ()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "hotel":
            kwargs["queryset"] = hotels_for_user(request.user)
        elif db_field.name == "user":
            staff_users = get_user_model().objects.filter(
                is_active=True, is_staff=True, is_superuser=False
            )
            if not request.user.is_superuser:
                staff_users = staff_users.exclude(
                    pk__in=HotelStaff.objects.exclude(
                        hotel__in=hotels_for_user(request.user)
                    ).values("user_id")
                )
            kwargs["queryset"] = staff_users
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(HousekeepingTask)
class HousekeepingTaskAdmin(admin.ModelAdmin):
    list_display = (
        "room",
        "task_type",
        "scheduled_for",
        "assigned_to",
        "status",
        "completed_at",
    )
    list_filter = ("task_type", "status", "scheduled_for")
    search_fields = (
        "room__room_number",
        "room__hotel__name",
        "assigned_to__username",
    )
    readonly_fields = ("completed_at", "created_at")
    date_hierarchy = "scheduled_for"

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            room__hotel__in=hotels_for_user(request.user)
        )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        hotels = hotels_for_user(request.user)
        if db_field.name == "room":
            kwargs["queryset"] = Room.objects.filter(hotel__in=hotels)
        elif db_field.name == "assigned_to":
            kwargs["queryset"] = get_user_model().objects.filter(
                is_active=True,
                is_staff=True,
                hotel_memberships__hotel__in=hotels,
            ).distinct()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Guest)
class GuestAdmin(admin.ModelAdmin):
    list_display = (
        "first_name",
        "last_name",
        "hotel",
        "email",
        "phone",
        "nationality",
    )
    search_fields = (
        "first_name",
        "last_name",
        "email",
        "phone",
        "document_number",
    )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        return queryset.filter(hotel__in=hotels_for_user(request.user))

    def has_add_permission(self, request):
        return request.user.is_superuser

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "hotel":
            kwargs["queryset"] = hotels_for_user(request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = (
        "guest",
        "room",
        "check_in",
        "check_out",
        "guest_count",
        "rate_per_night",
        "total_amount",
        "status",
    )
    list_filter = ("status", "room__hotel", "check_in")
    search_fields = (
        "guest__first_name",
        "guest__last_name",
        "room__room_number",
        "room__hotel__name",
    )
    date_hierarchy = "check_in"

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            room__hotel__in=hotels_for_user(request.user)
        )

    def get_list_filter(self, request):
        if request.user.is_superuser:
            return self.list_filter
        return ("status", "check_in")

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ("rate_per_night", "rate_currency", "total_amount")
        return ("total_amount",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        hotels = hotels_for_user(request.user)
        if db_field.name == "room":
            kwargs["queryset"] = Room.objects.filter(hotel__in=hotels)
        elif db_field.name == "guest" and not request.user.is_superuser:
            kwargs["queryset"] = Guest.objects.filter(
                hotel__in=hotels
            ).distinct()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not change:
            if obj.guest_count is None:
                obj.guest_count = 1
            if obj.rate_per_night is None:
                obj.rate_per_night = obj.room.room_type.base_rate
                obj.rate_currency = obj.room.room_type.rate_currency
        super().save_model(request, obj, form, change)
