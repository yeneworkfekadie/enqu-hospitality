from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Payment, Reservation


def verify_payment(*, payment_id: int, reviewed_by) -> Payment:
    expired_hold = False
    with transaction.atomic():
        payment = (
            Payment.objects.select_for_update()
            .select_related("reservation")
            .get(pk=payment_id)
        )
        reservation = Reservation.objects.select_for_update().get(
            pk=payment.reservation_id
        )

        if payment.status != Payment.Status.PENDING_REVIEW:
            raise ValidationError("Only payments pending review can be verified.")
        if reservation.status in (
            Reservation.Status.CANCELLED,
            Reservation.Status.EXPIRED,
            Reservation.Status.NO_SHOW,
        ):
            raise ValidationError("Payments cannot be verified for this reservation.")
        if (
            reservation.status == Reservation.Status.PENDING_PAYMENT
            and reservation.payment_expires_at <= timezone.now()
        ):
            reservation.status = Reservation.Status.EXPIRED
            reservation.save(update_fields=["status"])
            expired_hold = True
        else:
            verified_total = (
                Payment.objects.filter(
                    reservation=reservation,
                    status=Payment.Status.VERIFIED,
                ).aggregate(total=Sum("amount"))["total"]
                or 0
            )
            total_amount = reservation.total_amount
            if total_amount is None:
                raise ValidationError("This reservation has no saved total to pay.")
            if payment.amount <= 0:
                raise ValidationError("Payment amount must be greater than zero.")
            if payment.currency != reservation.rate_currency:
                raise ValidationError(
                    "Payment currency must match the reservation."
                )
            if verified_total + payment.amount > total_amount:
                raise ValidationError(
                    "Verified payments cannot exceed the reservation total."
                )

            payment.status = Payment.Status.VERIFIED
            payment.reviewed_by = reviewed_by
            payment.reviewed_at = timezone.now()
            payment.save(update_fields=["status", "reviewed_by", "reviewed_at"])

            if (
                reservation.status == Reservation.Status.PENDING_PAYMENT
                and verified_total + payment.amount == total_amount
            ):
                reservation.status = Reservation.Status.CONFIRMED
                reservation.save(update_fields=["status"])

    if expired_hold:
        raise ValidationError("The reservation payment hold has expired.")
    return payment


@transaction.atomic
def reject_payment(*, payment_id: int, reviewed_by) -> Payment:
    payment = Payment.objects.select_for_update().get(pk=payment_id)
    if payment.status != Payment.Status.PENDING_REVIEW:
        raise ValidationError("Only payments pending review can be rejected.")

    payment.status = Payment.Status.REJECTED
    payment.reviewed_by = reviewed_by
    payment.reviewed_at = timezone.now()
    payment.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    return payment
