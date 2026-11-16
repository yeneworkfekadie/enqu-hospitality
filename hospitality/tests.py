from datetime import date

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.urls import reverse

from .models import Guest, Hotel, Reservation, Room, RoomType
from .services import available_rooms


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


class BookingWorkflowTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != "postgresql":
            raise RuntimeError("Booking workflow tests require PostgreSQL.")

    def setUp(self):
        self.hotel = Hotel.objects.create(
            name="Lake Hotel", code="lake-hotel", city="Hawassa"
        )
        self.room_type = RoomType.objects.create(
            hotel=self.hotel, name="Standard", max_occupancy=2, base_rate="1000.00"
        )
        self.room = Room.objects.create(
            hotel=self.hotel, room_type=self.room_type, room_number="101"
        )
        user = get_user_model().objects.create_superuser(
            username="booking-admin",
            email="admin@example.com",
            password="test-password",
        )
        self.client.force_login(user)
        self.check_in = date(2026, 12, 1)
        self.check_out = date(2026, 12, 5)

    def test_availability_excludes_overlapping_active_reservations(self):
        guest = Guest.objects.create(first_name="Aster", last_name="Guest")
        Reservation.objects.create(
            room=self.room,
            guest=guest,
            check_in=date(2026, 12, 3),
            check_out=date(2026, 12, 6),
        )

        rooms = available_rooms(
            hotel=self.hotel, check_in=self.check_in, check_out=self.check_out
        )

        self.assertNotIn(self.room, rooms)

    def test_staff_can_search_and_open_an_available_room(self):
        response = self.client.get(
            reverse("hospitality:availability"),
            {
                "hotel": self.hotel.pk,
                "check_in": self.check_in,
                "check_out": self.check_out,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "room 101")
        self.assertContains(response, "Book this room")

        response = self.client.get(
            reverse("hospitality:create_booking"),
            {
                "hotel": self.hotel.pk,
                "check_in": self.check_in,
                "check_out": self.check_out,
                "room": self.room.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Guest first name")

    def test_availability_allows_adjacent_stays_and_excludes_maintenance(self):
        guest = Guest.objects.create(first_name="Aster", last_name="Guest")
        Reservation.objects.create(
            room=self.room,
            guest=guest,
            check_in=date(2026, 11, 28),
            check_out=self.check_in,
        )
        maintenance_room = Room.objects.create(
            hotel=self.hotel,
            room_type=self.room_type,
            room_number="102",
            status=Room.Status.MAINTENANCE,
        )

        rooms = available_rooms(
            hotel=self.hotel, check_in=self.check_in, check_out=self.check_out
        )

        self.assertIn(self.room, rooms)
        self.assertNotIn(maintenance_room, rooms)

    def test_staff_booking_creates_guest_and_reservation(self):
        response = self.client.post(
            reverse("hospitality:create_booking"),
            {
                "room": self.room.pk,
                "check_in": self.check_in,
                "check_out": self.check_out,
                "first_name": "Aster",
                "last_name": "Guest",
                "email": "aster@example.com",
                "phone": "555-0100",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reservation confirmed")
        reservation = Reservation.objects.get()
        self.assertEqual(reservation.room, self.room)
        self.assertEqual(reservation.guest.first_name, "Aster")

    def test_booking_rejects_a_room_that_became_unavailable(self):
        guest = Guest.objects.create(first_name="Dawit", last_name="Guest")
        Reservation.objects.create(
            room=self.room,
            guest=guest,
            check_in=self.check_in,
            check_out=self.check_out,
        )

        response = self.client.post(
            reverse("hospitality:create_booking"),
            {
                "room": self.room.pk,
                "check_in": self.check_in,
                "check_out": self.check_out,
                "first_name": "Aster",
                "last_name": "Guest",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This room is no longer available.")
        self.assertEqual(Guest.objects.count(), 1)

    def test_availability_page_requires_booking_permissions(self):
        user = get_user_model().objects.create_user(
            username="unprivileged", password="test-password", is_staff=True
        )
        self.client.force_login(user)

        response = self.client.get(reverse("hospitality:availability"))

        self.assertEqual(response.status_code, 403)
