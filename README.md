# Enku Hospitality

A Django foundation for a multi-property hospitality platform. The first
milestone covers hotels, room types, rooms, guests, and reservations, backed by
PostgreSQL.

The Django project package is named `enku_hospitality`.

## Run locally with Docker

1. Install and start Docker Desktop.
2. From this directory, run:

   ```powershell
   docker compose up --build
   ```

3. Open <http://localhost:8001/admin/>. Create an administrator in another
   terminal with:

   ```powershell
   docker compose exec web python manage.py createsuperuser
   ```

The development database is available to the web container through Docker's
internal network and is not exposed directly on your computer. Change the
database credentials and set a strong `DJANGO_SECRET_KEY` before deploying
anywhere outside local development.

## Run without Docker

Create a virtual environment, install `requirements.txt`, and set the
`POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, and
`POSTGRES_PORT` environment variables for a PostgreSQL database. Then run:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py runserver
```

Room reservations use half-open date ranges (`check_in` inclusive,
`check_out` exclusive), so one guest can check out on the day another checks
in. PostgreSQL enforces non-overlapping confirmed or checked-in reservations
for each room, including concurrent booking attempts.

## Staff booking workflow

After adding hotels, room types, and rooms in the admin, sign in with a staff
account assigned to the hotel and granted permission to view rooms and add
guests and reservations. A superuser can create the first assignment under
**Hotel staff assignments** in the admin. To let owners manage assignments for
their hotels, grant the **Hotel Owner** group view, add, change, and delete
permissions for hotel staff assignments. Open
<http://localhost:8001/bookings/> to search by hotel, dates, and guest count.
The booking flow checks room capacity, overlapping stays, and maintenance
status, then saves the room's nightly rate and currency on the reservation.
Existing reservations retain a blank rate if it was not recorded when they
were created.

Hotel assignments scope staff access to their assigned properties. Staff
groups still control which actions they can perform. Existing guest records
are assigned to a hotel when their reservations identify exactly one hotel;
unassigned or multi-hotel guest records remain visible only to superusers
until resolved. The booking page is currently staff-only; guest self-service
booking and Telebirr payments are planned for later phases.

## Housekeeping

Housekeeping tasks can be scheduled, assigned to staff assigned to the room's
hotel, and tracked through the Django admin. Completing a cleaning task marks
a dirty room clean without overriding occupied, reserved, or maintenance
status; completing an inspection does not change room status. Staff task lists
are scoped to their assigned hotels.
