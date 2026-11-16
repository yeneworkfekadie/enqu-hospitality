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
account that has permission to view rooms and add guests and reservations.
Open <http://localhost:8001/bookings/> to search available rooms and create a
reservation with guest details. The booking flow checks for overlapping stays
and excludes rooms under maintenance. It does not yet collect party size or
save a quoted room rate.
