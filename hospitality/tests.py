from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Guest,
    Hotel,
    HotelStaff,
    HousekeepingTask,
    Payment,
    Reservation,
    Room,
    RoomType,
)
from .services import available_rooms
from .payments import reject_payment, verify_payment


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

    def test_database_rejects_overlap_with_pending_payment_reservation(self):
        self.make_reservation(
            self.first_guest,
            date(2026, 12, 1),
            date(2026, 12, 5),
            status=Reservation.Status.PENDING_PAYMENT,
            payment_expires_at=timezone.now() + timedelta(minutes=15),
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.make_reservation(
                    self.second_guest, date(2026, 12, 3), date(2026, 12, 7)
                )


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

    def test_pending_payment_holds_room_until_expiry(self):
        guest = Guest.objects.create(
            hotel=self.hotel, first_name="Aster", last_name="Guest"
        )
        reservation = Reservation.objects.create(
            room=self.room,
            guest=guest,
            check_in=self.check_in,
            check_out=self.check_out,
            status=Reservation.Status.PENDING_PAYMENT,
            payment_expires_at=timezone.now() + timedelta(minutes=15),
        )

        self.assertNotIn(
            self.room,
            available_rooms(
                hotel=self.hotel,
                check_in=self.check_in,
                check_out=self.check_out,
            ),
        )

        reservation.payment_expires_at = timezone.now() - timedelta(seconds=1)
        reservation.save(update_fields=["payment_expires_at"])
        rooms = available_rooms(
            hotel=self.hotel,
            check_in=self.check_in,
            check_out=self.check_out,
        )
        reservation.refresh_from_db()

        self.assertIn(self.room, rooms)
        self.assertEqual(reservation.status, Reservation.Status.EXPIRED)
        next_guest = Guest.objects.create(
            hotel=self.hotel, first_name="Dawit", last_name="Guest"
        )
        next_reservation = Reservation.objects.create(
            room=self.room,
            guest=next_guest,
            check_in=self.check_in,
            check_out=self.check_out,
        )
        self.assertEqual(next_reservation.status, Reservation.Status.CONFIRMED)

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


class PublicBookingTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != "postgresql":
            raise RuntimeError("Public booking tests require PostgreSQL.")

    def setUp(self):
        self.hotel = Hotel.objects.create(
            name="Lake Hotel", code="lake-hotel", city="Hawassa"
        )
        room_type = RoomType.objects.create(
            hotel=self.hotel, name="Standard", max_occupancy=2, base_rate="1000.00"
        )
        self.room = Room.objects.create(
            hotel=self.hotel, room_type=room_type, room_number="101"
        )
        self.check_in = date(2026, 12, 1)
        self.check_out = date(2026, 12, 5)

    def booking_data(self):
        return {
            "room": self.room.pk,
            "check_in": self.check_in,
            "check_out": self.check_out,
            "guest_count": 2,
            "first_name": "Aster",
            "last_name": "Guest",
            "email": "aster@example.com",
            "phone": "555-0100",
        }

    def test_public_search_is_available_without_login(self):
        response = self.client.get(
            reverse("hospitality:public_availability"),
            {
                "hotel": self.hotel.pk,
                "check_in": self.check_in,
                "check_out": self.check_out,
                "guest_count": 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.hotel.name)
        self.assertContains(response, "room 101")
        self.assertContains(response, "Select room")

        response = self.client.get(
            reverse("hospitality:create_public_booking"),
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
        self.assertContains(response, "Place 15-minute hold")

    def test_public_booking_creates_a_pending_payment_hold(self):
        before = timezone.now()
        response = self.client.post(
            reverse("hospitality:create_public_booking"), self.booking_data()
        )
        after = timezone.now()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Room held pending payment")
        self.assertContains(response, "not confirmed")
        reservation = Reservation.objects.get()
        self.assertEqual(reservation.status, Reservation.Status.PENDING_PAYMENT)
        self.assertEqual(reservation.guest.hotel, self.hotel)
        self.assertEqual(reservation.total_amount, Decimal("4000.00"))
        self.assertGreaterEqual(
            reservation.payment_expires_at,
            before + timedelta(minutes=15),
        )
        self.assertLessEqual(
            reservation.payment_expires_at,
            after + timedelta(minutes=15),
        )

    def test_public_booking_requires_an_email_address(self):
        booking_data = self.booking_data()
        booking_data["email"] = ""

        response = self.client.post(
            reverse("hospitality:create_public_booking"), booking_data
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
        self.assertEqual(Reservation.objects.count(), 0)
        self.assertEqual(Guest.objects.count(), 0)

    def test_expiration_command_releases_stale_holds(self):
        guest = Guest.objects.create(
            hotel=self.hotel, first_name="Aster", last_name="Guest"
        )
        reservation = Reservation.objects.create(
            room=self.room,
            guest=guest,
            check_in=self.check_in,
            check_out=self.check_out,
            status=Reservation.Status.PENDING_PAYMENT,
            payment_expires_at=timezone.now() - timedelta(seconds=1),
        )
        output = StringIO()

        call_command("expire_pending_reservations", stdout=output)
        reservation.refresh_from_db()

        self.assertEqual(reservation.status, Reservation.Status.EXPIRED)
        self.assertIn("Expired 1", output.getvalue())


class PaymentVerificationTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != "postgresql":
            raise RuntimeError("Payment verification tests require PostgreSQL.")

    def setUp(self):
        self.hotel = Hotel.objects.create(
            name="Lake Hotel", code="lake-hotel", city="Hawassa"
        )
        room_type = RoomType.objects.create(
            hotel=self.hotel, name="Standard", max_occupancy=2, base_rate="1000.00"
        )
        self.room = Room.objects.create(
            hotel=self.hotel, room_type=room_type, room_number="101"
        )
        self.guest = Guest.objects.create(
            hotel=self.hotel, first_name="Aster", last_name="Guest"
        )
        self.reservation = Reservation.objects.create(
            room=self.room,
            guest=self.guest,
            check_in=date(2026, 12, 1),
            check_out=date(2026, 12, 5),
            guest_count=1,
            rate_per_night=Decimal("1000.00"),
            rate_currency="ETB",
            status=Reservation.Status.PENDING_PAYMENT,
            payment_expires_at=timezone.now() + timedelta(minutes=15),
        )
        self.user = get_user_model().objects.create_user(
            username="payment-reviewer", is_staff=True
        )

    def create_payment(self, amount, **kwargs):
        values = {
            "reservation": self.reservation,
            "amount": Decimal(amount),
            "currency": "ETB",
            "method": Payment.Method.CASH,
            "recorded_by": self.user,
        }
        values.update(kwargs)
        return Payment.objects.create(**values)

    def test_partial_verified_payment_keeps_reservation_pending(self):
        payment = self.create_payment("1500.00")

        verify_payment(payment_id=payment.pk, reviewed_by=self.user)

        self.reservation.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.VERIFIED)
        self.assertEqual(payment.reviewed_by, self.user)
        self.assertIsNotNone(payment.reviewed_at)
        self.assertEqual(self.reservation.status, Reservation.Status.PENDING_PAYMENT)
        self.assertEqual(self.reservation.verified_paid_amount, Decimal("1500.00"))
        self.assertEqual(self.reservation.balance_due, Decimal("2500.00"))

    def test_full_verified_payment_confirms_pending_reservation(self):
        payment = self.create_payment("4000.00", method=Payment.Method.BANK_TRANSFER)

        verify_payment(payment_id=payment.pk, reviewed_by=self.user)

        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.CONFIRMED)
        self.assertEqual(self.reservation.balance_due, Decimal("0.00"))

    def test_pending_payment_does_not_count_toward_balance(self):
        self.create_payment("4000.00")

        self.assertEqual(self.reservation.verified_paid_amount, Decimal("0.00"))
        self.assertEqual(self.reservation.balance_due, Decimal("4000.00"))
        self.assertEqual(
            self.reservation.status,
            Reservation.Status.PENDING_PAYMENT,
        )

    def test_verification_rejects_overpayment(self):
        first_payment = self.create_payment("3000.00")
        verify_payment(payment_id=first_payment.pk, reviewed_by=self.user)
        second_payment = self.create_payment("1500.00")

        with self.assertRaisesMessage(
            ValidationError, "Verified payments cannot exceed the reservation total."
        ):
            verify_payment(payment_id=second_payment.pk, reviewed_by=self.user)

        second_payment.refresh_from_db()
        self.assertEqual(second_payment.status, Payment.Status.PENDING_REVIEW)
        self.assertEqual(self.reservation.balance_due, Decimal("1000.00"))

    def test_verification_rejects_currency_mismatch(self):
        payment = self.create_payment("100.00", currency="USD")

        with self.assertRaisesMessage(
            ValidationError, "Payment currency must match the reservation."
        ):
            verify_payment(payment_id=payment.pk, reviewed_by=self.user)

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING_REVIEW)

    def test_expired_hold_is_expired_and_payment_cannot_be_verified(self):
        payment = self.create_payment("4000.00")
        self.reservation.payment_expires_at = timezone.now() - timedelta(seconds=1)
        self.reservation.save(update_fields=["payment_expires_at"])

        with self.assertRaisesMessage(
            ValidationError, "The reservation payment hold has expired."
        ):
            verify_payment(payment_id=payment.pk, reviewed_by=self.user)

        self.reservation.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.EXPIRED)
        self.assertEqual(payment.status, Payment.Status.PENDING_REVIEW)

    def test_rejecting_payment_is_audited_and_does_not_confirm_reservation(self):
        payment = self.create_payment("4000.00")

        reject_payment(payment_id=payment.pk, reviewed_by=self.user)

        self.reservation.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.REJECTED)
        self.assertEqual(payment.reviewed_by, self.user)
        self.assertIsNotNone(payment.reviewed_at)
        self.assertEqual(self.reservation.status, Reservation.Status.PENDING_PAYMENT)


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
        self.staff_user = user
        front_desk = Group.objects.create(name="Front Desk")
        permissions = Permission.objects.filter(
            content_type__app_label="hospitality",
            codename__in=[
                "add_guest",
                "add_reservation",
                "add_payment",
                "change_payment",
                "view_hotel",
                "view_guest",
                "view_housekeepingtask",
                "view_payment",
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

    def test_payment_admin_lists_only_payments_for_assigned_hotels(self):
        first_guest = Guest.objects.create(
            hotel=self.first_hotel, first_name="Aster", last_name="First"
        )
        second_guest = Guest.objects.create(
            hotel=self.second_hotel, first_name="Dawit", last_name="Second"
        )
        first_reservation = Reservation.objects.create(
            room=self.first_room,
            guest=first_guest,
            check_in=date(2026, 12, 1),
            check_out=date(2026, 12, 3),
            rate_per_night=Decimal("1000.00"),
            rate_currency="ETB",
        )
        second_reservation = Reservation.objects.create(
            room=self.second_room,
            guest=second_guest,
            check_in=date(2026, 12, 1),
            check_out=date(2026, 12, 3),
            rate_per_night=Decimal("1200.00"),
            rate_currency="ETB",
        )
        Payment.objects.create(
            reservation=first_reservation,
            amount=Decimal("100.00"),
            currency="ETB",
            method=Payment.Method.CASH,
            recorded_by=self.staff_user,
        )
        Payment.objects.create(
            reservation=second_reservation,
            amount=Decimal("100.00"),
            currency="ETB",
            method=Payment.Method.CASH,
            recorded_by=get_user_model().objects.create_user(
                username="other-hotel-cashier"
            ),
        )

        response = self.client.get(reverse("admin:hospitality_payment_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Aster First")
        self.assertNotContains(response, "Dawit Second")

    def test_staff_can_verify_a_payment_from_the_admin_action(self):
        guest = Guest.objects.create(
            hotel=self.first_hotel, first_name="Aster", last_name="First"
        )
        reservation = Reservation.objects.create(
            room=self.first_room,
            guest=guest,
            check_in=date(2026, 12, 1),
            check_out=date(2026, 12, 3),
            rate_per_night=Decimal("1000.00"),
            rate_currency="ETB",
            status=Reservation.Status.PENDING_PAYMENT,
            payment_expires_at=timezone.now() + timedelta(minutes=15),
        )
        payment = Payment.objects.create(
            reservation=reservation,
            amount=Decimal("2000.00"),
            currency="ETB",
            method=Payment.Method.TELEBIRR_MANUAL,
            recorded_by=self.staff_user,
            reference="manual-receipt-1",
        )

        response = self.client.post(
            reverse("admin:hospitality_payment_changelist"),
            {
                "action": "verify_selected_payments",
                "_selected_action": [payment.pk],
                "index": "0",
            },
        )

        self.assertEqual(response.status_code, 302)
        payment.refresh_from_db()
        reservation.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.VERIFIED)
        self.assertEqual(payment.reviewed_by, self.staff_user)
        self.assertEqual(reservation.status, Reservation.Status.CONFIRMED)
        self.assertEqual(
            self.client.get(
                reverse("admin:hospitality_payment_change", args=[payment.pk])
            ).status_code,
            200,
        )

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
