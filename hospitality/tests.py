from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.urls import reverse

from .models import (
    Guest,
    Hotel,
    HotelStaff,
    HousekeepingTask,
    Reservation,
    Room,
    RoomType,
)
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

    def test_availability_excludes_rooms_over_capacity(self):
        rooms = available_rooms(
            hotel=self.hotel,
            check_in=self.check_in,
            check_out=self.check_out,
            guest_count=3,
        )

        self.assertNotIn(self.room, rooms)

    def test_staff_can_search_and_open_an_available_room(self):
        response = self.client.get(
            reverse("hospitality:availability"),
            {
                "hotel": self.hotel.pk,
                "check_in": self.check_in,
                "check_out": self.check_out,
                "guest_count": 1,
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
                "guest_count": 1,
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
                "guest_count": 2,
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
        self.assertEqual(reservation.guest_count, 2)
        self.assertEqual(reservation.rate_per_night, Decimal("1000.00"))
        self.assertEqual(reservation.rate_currency, "ETB")
        self.assertEqual(reservation.total_amount, Decimal("4000.00"))

        self.room_type.base_rate = "1500.00"
        self.room_type.save()
        reservation.refresh_from_db()
        self.assertEqual(reservation.rate_per_night, Decimal("1000.00"))

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
                "guest_count": 1,
                "first_name": "Aster",
                "last_name": "Guest",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This room is no longer available.")
        self.assertEqual(Guest.objects.count(), 1)

    def test_booking_rejects_guest_count_above_room_capacity(self):
        response = self.client.post(
            reverse("hospitality:create_booking"),
            {
                "room": self.room.pk,
                "check_in": self.check_in,
                "check_out": self.check_out,
                "guest_count": 3,
                "first_name": "Aster",
                "last_name": "Guest",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Guest count exceeds this room type's maximum occupancy.",
            response.context["form"].errors["guest_count"],
        )
        self.assertEqual(Reservation.objects.count(), 0)
        self.assertEqual(Guest.objects.count(), 0)

    def test_availability_page_requires_booking_permissions(self):
        user = get_user_model().objects.create_user(
            username="unprivileged", password="test-password", is_staff=True
        )
        self.client.force_login(user)

        response = self.client.get(reverse("hospitality:availability"))

        self.assertEqual(response.status_code, 403)


class HousekeepingTaskTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != "postgresql":
            raise RuntimeError("Housekeeping task tests require PostgreSQL.")

    def setUp(self):
        self.hotel = Hotel.objects.create(
            name="Lake Hotel", code="lake-hotel", city="Hawassa"
        )
        room_type = RoomType.objects.create(
            hotel=self.hotel, name="Standard", max_occupancy=2, base_rate="1000.00"
        )
        self.room = Room.objects.create(
            hotel=self.hotel,
            room_type=room_type,
            room_number="101",
            status=Room.Status.DIRTY,
        )
        self.staff = get_user_model().objects.create_user(
            username="housekeeper", is_staff=True
        )
        HotelStaff.objects.create(user=self.staff, hotel=self.hotel)

    def test_completing_cleaning_task_marks_room_clean_and_records_time(self):
        task = HousekeepingTask.objects.create(room=self.room, assigned_to=self.staff)
        task.status = HousekeepingTask.Status.COMPLETED
        task.save()

        self.room.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(self.room.status, Room.Status.CLEAN)
        self.assertIsNotNone(task.completed_at)

    def test_inspection_does_not_change_room_cleanliness(self):
        task = HousekeepingTask.objects.create(
            room=self.room,
            assigned_to=self.staff,
            task_type=HousekeepingTask.TaskType.INSPECTION,
        )
        task.status = HousekeepingTask.Status.COMPLETED
        task.save()

        self.room.refresh_from_db()
        self.assertEqual(self.room.status, Room.Status.DIRTY)

    def test_cleaning_task_does_not_override_occupied_room_status(self):
        self.room.status = Room.Status.OCCUPIED
        self.room.save()
        task = HousekeepingTask.objects.create(room=self.room, assigned_to=self.staff)
        task.status = HousekeepingTask.Status.COMPLETED
        task.save()

        self.room.refresh_from_db()
        self.assertEqual(self.room.status, Room.Status.OCCUPIED)

    def test_staff_assignment_must_match_room_hotel(self):
        other_hotel = Hotel.objects.create(
            name="Mountain Hotel", code="mountain-hotel", city="Bahir Dar"
        )
        other_staff = get_user_model().objects.create_user(
            username="other-housekeeper", is_staff=True
        )
        HotelStaff.objects.create(user=other_staff, hotel=other_hotel)
        task = HousekeepingTask(room=self.room, assigned_to=other_staff)

        with self.assertRaisesMessage(
            ValidationError, "The staff member must be assigned to this room's hotel."
        ):
            task.full_clean()


class HotelAccessTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != "postgresql":
            raise RuntimeError("Hotel access tests require PostgreSQL.")

    def setUp(self):
        self.first_hotel = Hotel.objects.create(
            name="Lake Hotel", code="lake-hotel", city="Hawassa"
        )
        self.second_hotel = Hotel.objects.create(
            name="Mountain Hotel", code="mountain-hotel", city="Bahir Dar"
        )
        first_type = RoomType.objects.create(
            hotel=self.first_hotel,
            name="Standard",
            max_occupancy=2,
            base_rate="1000.00",
        )
        second_type = RoomType.objects.create(
            hotel=self.second_hotel,
            name="Standard",
            max_occupancy=2,
            base_rate="1200.00",
        )
        self.first_room = Room.objects.create(
            hotel=self.first_hotel, room_type=first_type, room_number="101"
        )
        self.second_room = Room.objects.create(
            hotel=self.second_hotel, room_type=second_type, room_number="201"
        )
        user = get_user_model().objects.create_user(
            username="front-desk", password="test-password", is_staff=True
        )
        front_desk = Group.objects.create(name="Front Desk")
        permissions = Permission.objects.filter(
            content_type__app_label="hospitality",
            codename__in=[
                "add_guest",
                "add_reservation",
                "view_hotel",
                "view_guest",
                "view_housekeepingtask",
                "view_reservation",
                "view_room",
                "view_roomtype",
            ],
        )
        front_desk.permissions.set(permissions)
        user.groups.add(front_desk)
        HotelStaff.objects.create(user=user, hotel=self.first_hotel)
        self.client.force_login(user)

    def test_availability_and_admin_rooms_are_scoped_to_assigned_hotels(self):
        response = self.client.get(
            reverse("hospitality:availability"),
            {
                "hotel": self.first_hotel.pk,
                "check_in": "2026-12-01",
                "check_out": "2026-12-03",
                "guest_count": 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "room 101")
        self.assertNotContains(response, "room 201")
        self.assertNotContains(response, self.second_hotel.name)

        response = self.client.get(reverse("admin:hospitality_room_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "101")
        self.assertNotContains(response, "201")

    def test_housekeeping_tasks_are_scoped_to_assigned_hotels(self):
        first_task = HousekeepingTask.objects.create(room=self.first_room)
        second_task = HousekeepingTask.objects.create(room=self.second_room)

        response = self.client.get(
            reverse("admin:hospitality_housekeepingtask_changelist")
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(first_task.room))
        self.assertNotContains(response, str(second_task.room))

    def test_guest_and_reservation_admin_lists_are_property_scoped(self):
        first_guest = Guest.objects.create(
            hotel=self.first_hotel, first_name="Aster", last_name="First"
        )
        second_guest = Guest.objects.create(
            hotel=self.second_hotel, first_name="Dawit", last_name="Second"
        )
        Reservation.objects.create(
            room=self.first_room,
            guest=first_guest,
            check_in=date(2026, 12, 1),
            check_out=date(2026, 12, 3),
        )
        Reservation.objects.create(
            room=self.second_room,
            guest=second_guest,
            check_in=date(2026, 12, 1),
            check_out=date(2026, 12, 3),
        )

        guest_response = self.client.get(reverse("admin:hospitality_guest_changelist"))
        reservation_response = self.client.get(
            reverse("admin:hospitality_reservation_changelist")
        )

        self.assertEqual(guest_response.status_code, 200)
        self.assertContains(guest_response, "Aster")
        self.assertNotContains(guest_response, "Dawit")
        self.assertEqual(reservation_response.status_code, 200)
        self.assertContains(reservation_response, "Aster")
        self.assertNotContains(reservation_response, "Dawit")

    def test_staff_cannot_book_a_room_at_an_unassigned_hotel(self):
        response = self.client.post(
            reverse("hospitality:create_booking"),
            {
                "room": self.second_room.pk,
                "check_in": "2026-12-01",
                "check_out": "2026-12-03",
                "guest_count": 1,
                "first_name": "Guest",
                "last_name": "Name",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Reservation.objects.count(), 0)
        self.assertEqual(Guest.objects.count(), 0)

    def test_owner_who_creates_a_hotel_is_assigned_to_it(self):
        user = get_user_model().objects.create_user(
            username="hotel-owner", password="test-password", is_staff=True
        )
        owner_group = Group.objects.create(name="Hotel Owner")
        owner_group.permissions.set(
            Permission.objects.filter(
                content_type__app_label="hospitality",
                codename__in=["add_hotel", "view_hotel"],
            )
        )
        user.groups.add(owner_group)
        self.client.force_login(user)

        response = self.client.post(
            reverse("admin:hospitality_hotel_add"),
            {
                "name": "New Hotel",
                "code": "new-hotel",
                "city": "Addis Ababa",
                "default_currency": "ETB",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        hotel = Hotel.objects.get(code="new-hotel")
        self.assertTrue(
            HotelStaff.objects.filter(user=user, hotel=hotel).exists()
        )
