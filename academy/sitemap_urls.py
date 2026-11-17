from django.urls import path

from .views import sitemap

urlpatterns = [path("", sitemap, name="academy-sitemap")]
