from django.db import migrations
from django.db.models import Count, Min


def assign_unambiguous_guest_hotels(apps, schema_editor):
    Guest = apps.get_model("hospitality", "Guest")
    Reservation = apps.get_model("hospitality", "Reservation")
    database = schema_editor.connection.alias

    reservations = (
        Reservation.objects.using(database)
        .values("guest_id")
        .annotate(
            hotel_count=Count("room__hotel_id", distinct=True),
            hotel_id=Min("room__hotel_id"),
        )
        .filter(hotel_count=1)
    )
    for row in reservations.iterator():
        Guest.objects.using(database).filter(
            pk=row["guest_id"], hotel__isnull=True
        ).update(hotel_id=row["hotel_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("hospitality", "0004_guest_hotel"),
    ]

    operations = [
        migrations.RunPython(
            assign_unambiguous_guest_hotels,
            migrations.RunPython.noop,
        ),
    ]
