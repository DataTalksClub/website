from django.urls import path

from . import admin_api_views

app_name = "api"

urlpatterns = [path("health", admin_api_views.admin_health, name="admin-health")]
