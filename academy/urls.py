from django.urls import path

from .views import resource

app_name = "academy"
urlpatterns = [
    path("", resource, name="home"),
    path("<path:path>", resource, name="resource"),
]
