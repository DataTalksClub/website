from django.urls import path

from . import fixture_views

urlpatterns = [
    path("studio/_fixtures/csrf/", fixture_views.csrf_seed, name="fixture-csrf"),
    path("studio/_fixtures/high-risk/", fixture_views.high_risk_post, name="fixture-high-risk"),
]
