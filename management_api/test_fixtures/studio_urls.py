from django.urls import path

from . import studio_views

app_name = "studio"

urlpatterns = [
    path("", studio_views.credential_lifecycle, name="home"),
    path("away/", studio_views.credential_away, name="credential-away"),
]
