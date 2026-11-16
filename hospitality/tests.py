from datetime import date

from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from .models import Guest, Hotel, Reservation, Room, RoomType


class ReservationConstraintTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != "postgresql":
            raise RuntimeError("Reservation overlap tests require PostgreSQL.")

    def setUp(self):
        hotel = Hotel.objects.create(name="Lake Hotel", code="lake-hotel", city="Hawassa")
        room_type = RoomType.objects.create(
            hotel=hotel, name="Standard", max_occupancy=2, base_rate="1000.00"
        )
        self.room = Room.objects.create(
            hotel=hotel, room_type=room_type, room_number="101"
        )
        self.first_guest = Guest.objects.create(first_name="Aster", last_name="Guest")
        self.second_guest = Guest.objects.create(first_name="Dawit", last_name="Guest")

    def make_reservation(self, guest, check_in, check_out, **kwargs):
        return Reservation.objects.create(
            room=self.room,
            guest=guest,
            check_in=check_in,
            check_out=check_out,
            **kwargs,
        )

    def test_database_rejects_overlapping_active_reservations(self):
        self.make_reservation(
            self.first_guest, date(2026, 12, 1), date(2026, 12, 5)
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.make_reservation(
                    self.second_guest, date(2026, 12, 4), date(2026, 12, 7)
                )

    def test_adjacent_stays_do_not_overlap(self):
        self.make_reservation(
            self.first_guest, date(2026, 12, 1), date(2026, 12, 5)
        )
        adjacent = self.make_reservation(
            self.second_guest, date(2026, 12, 5), date(2026, 12, 7)
        )

        self.assertIsNotNone(adjacent.pk)

    def test_cancelled_reservation_does_not_block_dates(self):
        self.make_reservation(
            self.first_guest,
            date(2026, 12, 1),
            date(2026, 12, 5),
            status=Reservation.Status.CANCELLED,
        )
        active = self.make_reservation(
            self.second_guest, date(2026, 12, 3), date(2026, 12, 7)
        )

        self.assertIsNotNone(active.pk)
