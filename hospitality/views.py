from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.shortcuts import render

from .forms import AvailabilitySearchForm, ReservationBookingForm
from .models import Room
from .services import (
    PUBLIC_PAYMENT_HOLD_MINUTES,
    available_rooms,
    create_reservation,
)


BOOKING_PERMISSIONS = (
    "hospitality.view_room",
    "hospitality.add_guest",
    "hospitality.add_reservation",
)


def _booking_quote(room, check_in, check_out):
    if not room or not check_in or not check_out or check_out <= check_in:
        return None
    nights = (check_out - check_in).days
    rate = room.room_type.base_rate
    return {
        "nights": nights,
        "rate_per_night": rate,
        "currency": room.room_type.rate_currency,
        "total": rate * nights,
    }


@login_required(login_url="/admin/login/")
@permission_required(BOOKING_PERMISSIONS, raise_exception=True)
def availability(request):
    form = AvailabilitySearchForm(request.GET or None, user=request.user)
    rooms = None
    search = None

    if request.GET and form.is_valid():
        search = form.cleaned_data
        rooms = available_rooms(
            hotel=search["hotel"],
            check_in=search["check_in"],
            check_out=search["check_out"],
            guest_count=search["guest_count"],
        )

    return render(
        request,
        "hospitality/availability.html",
        {"form": form, "rooms": rooms, "search": search},
    )


@login_required(login_url="/admin/login/")
@permission_required(BOOKING_PERMISSIONS, raise_exception=True)
def create_booking(request):
    if request.method == "POST":
        form = ReservationBookingForm(request.POST, user=request.user)
        if form.is_valid():
            guest_data = {
                field: form.cleaned_data[field]
                for field in ("first_name", "last_name", "email", "phone")
            }
            try:
                reservation = create_reservation(
                    room=form.cleaned_data["room"],
                    check_in=form.cleaned_data["check_in"],
                    check_out=form.cleaned_data["check_out"],
                    guest_count=form.cleaned_data["guest_count"],
                    guest_data=guest_data,
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                return render(
                    request,
                    "hospitality/booking_success.html",
                    {"reservation": reservation},
                )

        room = form.cleaned_data.get("room")
        quote = _booking_quote(
            room,
            form.cleaned_data.get("check_in"),
            form.cleaned_data.get("check_out"),
        )
        return render(
            request,
            "hospitality/booking.html",
            {"form": form, "room": room, "quote": quote},
        )

    search_form = AvailabilitySearchForm(request.GET, user=request.user)
    if not search_form.is_valid():
        return render(
            request,
            "hospitality/availability.html",
            {"form": search_form, "rooms": None, "search": None},
            status=400,
        )

    search = search_form.cleaned_data
    room_id = request.GET.get("room", "")
    room = None
    if room_id.isdecimal():
        room = available_rooms(
            hotel=search["hotel"],
            check_in=search["check_in"],
            check_out=search["check_out"],
            guest_count=search["guest_count"],
        ).filter(pk=room_id).first()
    if room is None:
        return render(
            request,
            "hospitality/booking.html",
            {"room_unavailable": True},
            status=409,
        )

    form = ReservationBookingForm(
        user=request.user,
        initial={
            "room": room.pk,
            "check_in": search["check_in"],
            "check_out": search["check_out"],
            "guest_count": search["guest_count"],
        }
    )
    return render(
        request,
        "hospitality/booking.html",
        {
            "form": form,
            "room": room,
            "quote": _booking_quote(room, search["check_in"], search["check_out"]),
        },
    )


def public_availability(request):
    form = AvailabilitySearchForm(request.GET or None)
    rooms = None
    search = None

    if request.GET and form.is_valid():
        search = form.cleaned_data
        rooms = available_rooms(
            hotel=search["hotel"],
            check_in=search["check_in"],
            check_out=search["check_out"],
            guest_count=search["guest_count"],
        )

    return render(
        request,
        "hospitality/public_availability.html",
        {"form": form, "rooms": rooms, "search": search},
    )


def create_public_booking(request):
    if request.method == "POST":
        form = ReservationBookingForm(request.POST, public=True)
        if form.is_valid():
            guest_data = {
                field: form.cleaned_data[field]
                for field in ("first_name", "last_name", "email", "phone")
            }
            try:
                reservation = create_reservation(
                    room=form.cleaned_data["room"],
                    check_in=form.cleaned_data["check_in"],
                    check_out=form.cleaned_data["check_out"],
                    guest_count=form.cleaned_data["guest_count"],
                    guest_data=guest_data,
                    payment_hold_minutes=PUBLIC_PAYMENT_HOLD_MINUTES,
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                return render(
                    request,
                    "hospitality/public_booking_success.html",
                    {"reservation": reservation},
                )

        room = form.cleaned_data.get("room")
        quote = _booking_quote(
            room,
            form.cleaned_data.get("check_in"),
            form.cleaned_data.get("check_out"),
        )
        return render(
            request,
            "hospitality/public_booking.html",
            {
                "form": form,
                "room": room,
                "quote": quote,
                "hold_minutes": PUBLIC_PAYMENT_HOLD_MINUTES,
            },
        )

    search_form = AvailabilitySearchForm(request.GET or None)
    if not search_form.is_valid():
        return render(
            request,
            "hospitality/public_availability.html",
            {"form": search_form, "rooms": None, "search": None},
            status=400,
        )

    search = search_form.cleaned_data
    room_id = request.GET.get("room", "")
    room = None
    if room_id.isdecimal():
        room = available_rooms(
            hotel=search["hotel"],
            check_in=search["check_in"],
            check_out=search["check_out"],
            guest_count=search["guest_count"],
        ).filter(pk=room_id).first()
    if room is None:
        return render(
            request,
            "hospitality/public_booking.html",
            {"room_unavailable": True},
            status=409,
        )

    form = ReservationBookingForm(
        public=True,
        initial={
            "room": room.pk,
            "check_in": search["check_in"],
            "check_out": search["check_out"],
            "guest_count": search["guest_count"],
        },
    )
    return render(
        request,
        "hospitality/public_booking.html",
        {
            "form": form,
            "room": room,
            "quote": _booking_quote(room, search["check_in"], search["check_out"]),
            "hold_minutes": PUBLIC_PAYMENT_HOLD_MINUTES,
        },
    )
