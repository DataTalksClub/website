from django.urls import path

from . import views

urlpatterns = [
    path("health", views.admin_health, name="admin-health"),
]
