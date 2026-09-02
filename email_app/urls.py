"""Public routes Relay renders into mail.

These three paths are not a website design choice: Relay builds them from its own
``PUBLIC_BASE_URL`` (``mailing/services/public_urls.py``), so their shape belongs
to Relay and must match it exactly.

``PUBLIC_BASE_URL`` names the host that serves them, and that host changes once.
Until the stage-2 apex swap, ``datatalks.club`` ALIASes the legacy static site
and cannot serve a Django route, so the value is ``https://prod.datatalks.club``;
after the swap it becomes ``https://datatalks.club``.  Neither value appears in
this application: the routes are host-independent and nothing here builds an
absolute URL from a request host.
"""

from django.urls import path

from email_app import views

urlpatterns = [
    path("t/o/<str:tracking_token>.gif", views.tracking_open, name="relay-tracking-open"),
    path("t/c/<str:tracking_token>", views.tracking_click, name="relay-tracking-click"),
    path(
        "unsubscribe/<str:unsubscribe_token>",
        views.public_unsubscribe,
        name="relay-public-unsubscribe",
    ),
]
