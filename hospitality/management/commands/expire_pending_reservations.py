from django.core.management.base import BaseCommand

from hospitality.services import expire_pending_reservations


class Command(BaseCommand):
    help = "Expire pending reservation payment holds that have passed their deadline."

    def handle(self, *args, **options):
        expired_count = expire_pending_reservations()
        self.stdout.write(
            self.style.SUCCESS(
                f"Expired {expired_count} pending reservation payment hold(s)."
            )
        )
