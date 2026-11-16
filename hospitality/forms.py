from django import forms

from .access import hotels_for_user
from .models import Hotel, Room
from .services import available_rooms


class AvailabilitySearchForm(forms.Form):
    hotel = forms.ModelChoiceField(queryset=Hotel.objects.none())
    check_in = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    check_out = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    guest_count = forms.IntegerField(min_value=1, initial=1, label="Number of guests")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        hotels = Hotel.objects.filter(is_active=True)
        if user is not None:
            hotels = hotels.filter(pk__in=hotels_for_user(user))
        self.fields["hotel"].queryset = hotels

    def clean(self):
        cleaned_data = super().clean()
        check_in = cleaned_data.get("check_in")
        check_out = cleaned_data.get("check_out")
        if check_in and check_out and check_out <= check_in:
            self.add_error("check_out", "Check-out must be later than check-in.")
        return cleaned_data


class ReservationBookingForm(forms.Form):
    room = forms.ModelChoiceField(
        queryset=Room.objects.none(),
        widget=forms.HiddenInput,
    )
    check_in = forms.DateField(widget=forms.HiddenInput)
    check_out = forms.DateField(widget=forms.HiddenInput)
    guest_count = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    first_name = forms.CharField(max_length=100, label="Guest first name")
    last_name = forms.CharField(max_length=100, label="Guest last name")
    email = forms.EmailField(required=False)
    phone = forms.CharField(max_length=30, required=False)

    def __init__(self, *args, user=None, public=False, **kwargs):
        super().__init__(*args, **kwargs)
        if public:
            self.fields["room"].queryset = Room.objects.filter(
                hotel__is_active=True
            ).select_related("hotel", "room_type")
            self.fields["email"].required = True
        elif user is not None:
            self.fields["room"].queryset = Room.objects.filter(
                hotel__in=hotels_for_user(user)
            ).select_related("hotel", "room_type")
        else:
            raise TypeError("Pass a staff user or set public=True.")

    def clean(self):
        cleaned_data = super().clean()
        room = cleaned_data.get("room")
        check_in = cleaned_data.get("check_in")
        check_out = cleaned_data.get("check_out")
        guest_count = cleaned_data.get("guest_count")

        if check_in and check_out and check_out <= check_in:
            self.add_error("check_out", "Check-out must be later than check-in.")
        elif room and check_in and check_out and guest_count:
            if guest_count > room.room_type.max_occupancy:
                self.add_error(
                    "guest_count",
                    "Guest count exceeds this room type's maximum occupancy.",
                )
                return cleaned_data
            if not available_rooms(
                hotel=room.hotel,
                check_in=check_in,
                check_out=check_out,
                guest_count=guest_count,
            ).filter(pk=room.pk).exists():
                self.add_error("room", "This room is no longer available.")

        return cleaned_data
