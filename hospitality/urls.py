from django.urls import path

from . import views


app_name = "hospitality"

urlpatterns = [
    path("", views.availability, name="availability"),
    path("new/", views.create_booking, name="create_booking"),
]
