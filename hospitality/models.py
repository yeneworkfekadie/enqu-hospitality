from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.contrib.postgres.fields.ranges import DateRangeField
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Func, Q, Value


class Currency(models.TextChoices):
    ETB = "ETB", "Ethiopian birr"
    USD = "USD", "US dollar"
    EUR = "EUR", "Euro"


class Hotel(models.Model):
    name = models.CharField(max_length=160)
    code = models.SlugField(max_length=40, unique=True)
    city = models.CharField(max_length=100)
    default_currency = models.CharField(
        max_length=3, choices=Currency.choices, default=Currency.ETB
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class RoomType(models.Model):
    hotel = models.ForeignKey(Hotel, on_delete=models.CASCADE, related_name="room_types")
    name = models.CharField(max_length=100)
    max_occupancy = models.PositiveSmallIntegerField(default=2)
    base_rate = models.DecimalField(max_digits=12, decimal_places=2)
    rate_currency = models.CharField(
        max_length=3, choices=Currency.choices, default=Currency.ETB
    )

    class Meta:
        ordering = ["hotel", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["hotel", "name"], name="unique_room_type_per_hotel"
            ),
            models.CheckConstraint(
                condition=Q(max_occupancy__gte=1), name="room_type_occupancy_positive"
            ),
            models.CheckConstraint(
                condition=Q(base_rate__gte=0), name="room_type_rate_nonnegative"
            ),
        ]

    def __str__(self):
        return f"{self.hotel}: {self.name}"


class Room(models.Model):
    class Status(models.TextChoices):
        CLEAN = "clean", "Clean"
        DIRTY = "dirty", "Dirty"
        OCCUPIED = "occupied", "Occupied"
        MAINTENANCE = "maintenance", "Maintenance"
        RESERVED = "reserved", "Reserved"

    hotel = models.ForeignKey(Hotel, on_delete=models.CASCADE, related_name="rooms")
    room_type = models.ForeignKey(
        RoomType, on_delete=models.PROTECT, related_name="rooms"
    )
    room_number = models.CharField(max_length=20)
    floor = models.CharField(max_length=20, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.CLEAN
    )

    class Meta:
        ordering = ["hotel", "room_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["hotel", "room_number"], name="unique_room_number_per_hotel"
            )
        ]

    def clean(self):
        super().clean()
        if self.hotel_id and self.room_type_id:
            if self.hotel_id != self.room_type.hotel_id:
                raise ValidationError(
                    {"room_type": "The room type must belong to the selected hotel."}
                )

    def __str__(self):
        return f"{self.hotel} / {self.room_number}"


class Guest(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    nationality = models.CharField(max_length=80, blank=True)
    document_type = models.CharField(max_length=30, blank=True)
    document_number = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


class Reservation(models.Model):
    class Status(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmed"
        CHECKED_IN = "checked_in", "Checked in"
        CHECKED_OUT = "checked_out", "Checked out"
        CANCELLED = "cancelled", "Cancelled"
        NO_SHOW = "no_show", "No show"

    room = models.ForeignKey(Room, on_delete=models.PROTECT, related_name="reservations")
    guest = models.ForeignKey(
        Guest, on_delete=models.PROTECT, related_name="reservations"
    )
    check_in = models.DateField()
    check_out = models.DateField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.CONFIRMED
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["check_in", "room"]
        constraints = [
            models.CheckConstraint(
                condition=Q(check_out__gt=F("check_in")),
                name="reservation_checkout_after_checkin",
            ),
            ExclusionConstraint(
                name="exclude_overlapping_active_room_reservations",
                expressions=[
                    (F("room"), RangeOperators.EQUAL),
                    (
                        Func(
                            F("check_in"),
                            F("check_out"),
                            Value("[)"),
                            function="DATERANGE",
                            output_field=DateRangeField(),
                        ),
                        RangeOperators.OVERLAPS,
                    ),
                ],
                condition=Q(status__in=["confirmed", "checked_in"]),
            ),
        ]

    def clean(self):
        super().clean()
        if self.check_in and self.check_out and self.check_out <= self.check_in:
            raise ValidationError(
                {"check_out": "Check-out must be later than check-in."}
            )
        if self.room_id and self.room.status == Room.Status.MAINTENANCE:
            raise ValidationError(
                {"room": "A room under maintenance cannot be reserved."}
            )

    def __str__(self):
        return f"{self.guest} - {self.room} ({self.check_in} to {self.check_out})"
