import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "enku_hospitality.settings")

application = get_wsgi_application()
