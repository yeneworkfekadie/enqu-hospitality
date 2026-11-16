from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, QuerySet

from .models import Guest, Hotel, Reservation, Room


ACTIVE_RESERVATION_STATUSES = (
    Reservation.Status.CONFIRMED,
    Reservation.Status.CHECKED_IN,
)


def available_rooms(
    *, hotel: Hotel, check_in: date, check_out: date
) -> QuerySet[Room]:
    if check_out <= check_in:
        raise ValidationError({"check_out": "Check-out must be later than check-in."})

    overlapping_reservations = Reservation.objects.filter(
        room_id=OuterRef("pk"),
        status__in=ACTIVE_RESERVATION_STATUSES,
        check_in__lt=check_out,
        check_out__gt=check_in,
    )
    return (
        Room.objects.filter(
            hotel=hotel,
            hotel__is_active=True,
            room_type__hotel=hotel,
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
    guest_data: dict[str, str],
) -> Reservation:
    if check_out <= check_in:
        raise ValidationError({"check_out": "Check-out must be later than check-in."})

    room = Room.objects.select_for_update().select_related("hotel").get(pk=room.pk)
    if not available_rooms(
        hotel=room.hotel, check_in=check_in, check_out=check_out
    ).filter(pk=room.pk).exists():
        raise ValidationError("This room is no longer available for those dates.")

    guest = Guest.objects.create(**guest_data)
    reservation = Reservation(
        room=room,
        guest=guest,
        check_in=check_in,
        check_out=check_out,
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
