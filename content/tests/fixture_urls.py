from django.urls import path

from .fixture_views import fixture_asset, fixture_document

urlpatterns = [
    path("Fixture/Exact.html", fixture_document),
    path("assets/Fixture-Logo.svg", fixture_asset),
]

handler404 = "content.tests.fixture_views.not_found"
