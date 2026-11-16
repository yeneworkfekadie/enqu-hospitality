from django.contrib import admin
from django.urls import path


admin.site.site_header = "Enku Hospitality"
admin.site.site_title = "Enku Hospitality"
admin.site.index_title = "Hotel management"

urlpatterns = [
    path("admin/", admin.site.urls),
]
