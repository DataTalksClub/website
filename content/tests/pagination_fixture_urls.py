from django.urls import path

from website.urls import urlpatterns as website_urlpatterns

from .pagination_fixture_views import pagination_fixture

urlpatterns = [
    path("blog", pagination_fixture, {"fixture": "blog"}),
    path("books", pagination_fixture, {"fixture": "books"}),
    path("events/past", pagination_fixture, {"fixture": "events"}),
    path("wiki", pagination_fixture, {"fixture": "wiki"}),
    *website_urlpatterns,
]
