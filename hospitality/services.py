from datetime import date, datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils import timezone

from .models import Guest, Hotel, Reservation, Room


ACTIVE_RESERVATION_STATUSES = (
    Reservation.Status.CONFIRMED,
    Reservation.Status.CHECKED_IN,
)
PUBLIC_PAYMENT_HOLD_MINUTES = 15


def expire_pending_reservations(
    *,
    now: datetime | None = None,
    hotel: Hotel | None = None,
    check_in: date | None = None,
    check_out: date | None = None,
) -> int:
    now = now or timezone.now()
    expired = Reservation.objects.filter(
        status=Reservation.Status.PENDING_PAYMENT,
        payment_expires_at__lte=now,
    )
    if hotel is not None:
        expired = expired.filter(room__hotel=hotel)
    if check_in is not None and check_out is not None:
        expired = expired.filter(check_in__lt=check_out, check_out__gt=check_in)
    return expired.update(status=Reservation.Status.EXPIRED)


def available_rooms(
    *, hotel: Hotel, check_in: date, check_out: date, guest_count: int = 1
) -> QuerySet[Room]:
    if check_out <= check_in:
        raise ValidationError({"check_out": "Check-out must be later than check-in."})
    if guest_count < 1:
        raise ValidationError({"guest_count": "Enter at least one guest."})

    expire_pending_reservations(hotel=hotel, check_in=check_in, check_out=check_out)
    overlapping_reservations = Reservation.objects.filter(
        room_id=OuterRef("pk"),
        check_in__lt=check_out,
        check_out__gt=check_in,
    ).filter(
        Q(status__in=ACTIVE_RESERVATION_STATUSES)
        | Q(status=Reservation.Status.PENDING_PAYMENT)
    )
    return (
        Room.objects.filter(
            hotel=hotel,
            hotel__is_active=True,
            room_type__hotel=hotel,
            room_type__max_occupancy__gte=guest_count,
        )
        .exclude(status=Room.Status.MAINTENANCE)
        .annotate(has_overlapping_reservation=Exists(overlapping_reservations))
        .filter(has_overlapping_reservation=False)
        .select_related("hotel", "room_type")
    )


@transaction.atomic
def create_reservation(
    *,
    room: Room,
    check_in: date,
    check_out: date,
    guest_count: int,
    guest_data: dict[str, str],
    payment_hold_minutes: int | None = None,
) -> Reservation:
    if check_out <= check_in:
        raise ValidationError({"check_out": "Check-out must be later than check-in."})
    if guest_count < 1:
        raise ValidationError({"guest_count": "Enter at least one guest."})
    if payment_hold_minutes is not None and payment_hold_minutes < 1:
        raise ValidationError(
            {"payment_hold_minutes": "Hold duration must be positive."}
        )

    room = Room.objects.select_for_update().select_related("hotel").get(pk=room.pk)
    if not available_rooms(
        hotel=room.hotel,
        check_in=check_in,
        check_out=check_out,
        guest_count=guest_count,
    ).filter(pk=room.pk).exists():
        raise ValidationError("This room is no longer available for those dates.")

    status = (
        Reservation.Status.PENDING_PAYMENT
        if payment_hold_minutes is not None
        else Reservation.Status.CONFIRMED
    )
    payment_expires_at = (
        timezone.now() + timedelta(minutes=payment_hold_minutes)
        if payment_hold_minutes is not None
        else None
    )
    guest = Guest.objects.create(hotel=room.hotel, **guest_data)
    reservation = Reservation(
        room=room,
        guest=guest,
        check_in=check_in,
        check_out=check_out,
        guest_count=guest_count,
        rate_per_night=room.room_type.base_rate,
        rate_currency=room.room_type.rate_currency,
        status=status,
        payment_expires_at=payment_expires_at,
    )
    reservation.full_clean()
    try:
        with transaction.atomic():
            reservation.save()
    except IntegrityError as error:
        cause = error.__cause__
        diagnostic = getattr(cause, "diag", None)
        if (
            getattr(diagnostic, "constraint_name", None)
            == "exclude_overlapping_active_room_reservations"
        ):
            raise ValidationError(
                "This room was just booked for those dates. Choose another room."
            ) from error
        raise

    return reservation
