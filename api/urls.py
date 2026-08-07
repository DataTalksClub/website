from django.urls import path

from . import views

app_name = "api"

urlpatterns = [path("health", views.admin_health, name="admin-health")]
