from django.urls import path

from .views import resource

urlpatterns = [path("", resource, {"path": "sitemap.xml"}, name="academy-sitemap")]
