from django import forms

from .models import Hotel, Room
from .services import available_rooms


class AvailabilitySearchForm(forms.Form):
    hotel = forms.ModelChoiceField(
        queryset=Hotel.objects.filter(is_active=True),
    )
    check_in = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    check_out = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        cleaned_data = super().clean()
        check_in = cleaned_data.get("check_in")
        check_out = cleaned_data.get("check_out")
        if check_in and check_out and check_out <= check_in:
            self.add_error("check_out", "Check-out must be later than check-in.")
        return cleaned_data


class ReservationBookingForm(forms.Form):
    room = forms.ModelChoiceField(
        queryset=Room.objects.select_related("hotel"),
        widget=forms.HiddenInput,
    )
    check_in = forms.DateField(widget=forms.HiddenInput)
    check_out = forms.DateField(widget=forms.HiddenInput)
    first_name = forms.CharField(max_length=100, label="Guest first name")
    last_name = forms.CharField(max_length=100, label="Guest last name")
    email = forms.EmailField(required=False)
    phone = forms.CharField(max_length=30, required=False)

    def clean(self):
        cleaned_data = super().clean()
        room = cleaned_data.get("room")
        check_in = cleaned_data.get("check_in")
        check_out = cleaned_data.get("check_out")

        if check_in and check_out and check_out <= check_in:
            self.add_error("check_out", "Check-out must be later than check-in.")
        elif room and check_in and check_out:
            if not available_rooms(
                hotel=room.hotel,
                check_in=check_in,
                check_out=check_out,
            ).filter(pk=room.pk).exists():
                self.add_error("room", "This room is no longer available.")

        return cleaned_data
