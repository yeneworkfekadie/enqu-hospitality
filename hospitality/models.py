from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.contrib.postgres.fields.ranges import DateRangeField
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Func, Q, Value
from django.utils import timezone


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


class HotelStaff(models.Model):
    hotel = models.ForeignKey(
        Hotel, on_delete=models.CASCADE, related_name="staff_memberships"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hotel_memberships",
    )

    class Meta:
        ordering = ["hotel", "user"]
        verbose_name = "hotel staff assignment"
        verbose_name_plural = "hotel staff assignments"
        constraints = [
            models.UniqueConstraint(
                fields=["hotel", "user"], name="unique_staff_assignment_per_hotel"
            )
        ]

    def __str__(self):
        return f"{self.user} - {self.hotel}"


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


class HousekeepingTask(models.Model):
    class TaskType(models.TextChoices):
        CLEANING = "cleaning", "Cleaning"
        INSPECTION = "inspection", "Inspection"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    room = models.ForeignKey(
        Room, on_delete=models.PROTECT, related_name="housekeeping_tasks"
    )
    task_type = models.CharField(
        max_length=20, choices=TaskType.choices, default=TaskType.CLEANING
    )
    scheduled_for = models.DateField(default=timezone.localdate)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="housekeeping_tasks",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["scheduled_for", "room"]

    def clean(self):
        super().clean()
        if self.assigned_to_id and self.room_id:
            is_assigned_to_hotel = self.assigned_to.hotel_memberships.filter(
                hotel_id=self.room.hotel_id
            ).exists()
            if not is_assigned_to_hotel:
                raise ValidationError(
                    {
                        "assigned_to": (
                            "The staff member must be assigned to this room's hotel."
                        )
                    }
                )
        if self.status == self.Status.COMPLETED and not self.completed_at:
            self.completed_at = timezone.now()
        elif self.status != self.Status.COMPLETED:
            self.completed_at = None

    @transaction.atomic
    def save(self, *args, **kwargs):
        previous_status = None
        if self.pk:
            previous_status = (
                type(self).objects.filter(pk=self.pk)
                .values_list("status", flat=True)
                .first()
            )
        if self.status == self.Status.COMPLETED and not self.completed_at:
            self.completed_at = timezone.now()
        elif self.status != self.Status.COMPLETED:
            self.completed_at = None

        super().save(*args, **kwargs)

        if (
            self.status == self.Status.COMPLETED
            and previous_status != self.Status.COMPLETED
            and self.task_type == self.TaskType.CLEANING
        ):
            Room.objects.filter(
                pk=self.room_id, status=Room.Status.DIRTY
            ).update(status=Room.Status.CLEAN)

    def __str__(self):
        return f"{self.get_task_type_display()} - {self.room} ({self.scheduled_for})"


class Guest(models.Model):
    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.PROTECT,
        related_name="guests",
        null=True,
        blank=True,
    )
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
    guest_count = models.PositiveSmallIntegerField(null=True, blank=True)
    rate_per_night = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    rate_currency = models.CharField(
        max_length=3, choices=Currency.choices, null=True, blank=True
    )
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
            models.CheckConstraint(
                condition=Q(guest_count__isnull=True) | Q(guest_count__gte=1),
                name="reservation_guest_count_positive",
            ),
            models.CheckConstraint(
                condition=Q(
                    rate_per_night__isnull=True, rate_currency__isnull=True
                )
                | Q(
                    rate_per_night__gte=0,
                    rate_currency__in=Currency.values,
                ),
                name="reservation_rate_and_currency_valid",
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
        if (
            self.guest_id
            and self.guest.hotel_id
            and self.room_id
            and self.guest.hotel_id != self.room.hotel_id
        ):
            raise ValidationError(
                {"guest": "The guest record belongs to a different hotel."}
            )
        if self.guest_count is not None:
            if self.guest_count < 1:
                raise ValidationError(
                    {"guest_count": "A reservation must include at least one guest."}
                )
            if (
                self.room_id
                and self.guest_count > self.room.room_type.max_occupancy
            ):
                raise ValidationError(
                    {
                        "guest_count": (
                            "Guest count exceeds this room type's maximum occupancy."
                        )
                    }
                )
        if (self.rate_per_night is None) != (self.rate_currency is None):
            raise ValidationError(
                "A saved nightly rate and currency must be provided together."
            )
        if self.rate_per_night is not None and self.rate_per_night < 0:
            raise ValidationError({"rate_per_night": "Rate cannot be negative."})

    @property
    def total_amount(self):
        if self.rate_per_night is None or not self.check_in or not self.check_out:
            return None
        return self.rate_per_night * (self.check_out - self.check_in).days

    def __str__(self):
        return f"{self.guest} - {self.room} ({self.check_in} to {self.check_out})"
